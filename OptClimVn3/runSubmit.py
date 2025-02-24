from __future__ import annotations

import logging
import typing

from SubmitStudy import SubmitStudy
import numpy as np
import pandas as pd
import optclim_exceptions
import warnings
import functools

my_logger = logging.getLogger(f"OPTCLIM.{__name__}")

from model_base import model_base


class provisional(model_base):
    rng: np.random.default_rng
    provisional_models: dict[str : pd.Series]
    create_models: bool
    max_provisional_cases: int
    provisional_count: int  # number of provisional cases made
    successful_provisional_count: int  # number of provisional cases used.
    mean: typing.Optional[pd.Series]
    cov: typing.Optional[pd.DataFrame]
    """ 
    Class to support provisional generation of models.  To be used within runSubmit
    Idea is that a model only gets created if key is present. Algorithm gets run twice.
    If key is not present then a random sample is generated. 
    Class objects have the following attributes:
    rng: a np.random.default_rng object
    provisional_keys: dict of keys and (random) observations. 
    max_provisional_cases: int -- the maximum number of provisional cases to generate.  
    provisional_count:int # number of provisional cases made
    successful_provisional_count:int # number of provisional cases used. 
 
    """

    def __init__(
        self,
        seed: typing.Optional[int] = None,
        max_provisional_cases: typing.Optional[int] = None,
        mean: typing.Optional[pd.Series] = None,
        cov: typing.Optional[pd.DataFrame] = None,
    ):
        self.rng = np.random.default_rng(seed=seed)
        self.provisional_models = dict()
        self.create_models = False
        self.max_provisional_cases = max_provisional_cases
        self.provisional_count = 0
        self.successful_provisional_count = 0
        if (cov is not None) and (
            mean is not None
        ):  # check cov and mean are compatable
            if cov.shape[0] != cov.shape[1]:
                raise ValueError("Cov not square")
            if cov.shape[0] != mean.shape[0]:
                raise ValueError("Cov not compatible with mean")
            if not np.all(cov.index == cov.columns):
                raise ValueError("cov index != cov.columns")
            if not np.all(mean.index == cov.index):
                raise ValueError("Mean and covariance not compatible")
        self.mean = mean
        self.cov = cov

    def set_for_provisional(self) -> None:
        """
        Set up provisional to generate provisional cases
        :return: None
        """
        self.provisional_models = dict()  # now an empty dict.
        self.create_models = False  # cannot create models

    def set_for_create(self) -> None:
        """
        Set up provisional to create models
        :return: Nothing

        """
        self.create_models = True  # can create models.

    def __eq__(self, other):
        """
        Test for equality. provisional objects are equal if they are the same type,
        and have the same value for all attributes except for rng where
         we require the state to be the same.
        :param other:
        :return:
        """
        equal = isinstance(other, type(self))
        if not equal:
            my_logger.debug("Types differ")
            return equal

        for attr in vars(self).keys():  # loop over attributes

            sattr = getattr(self, attr)
            oattr = getattr(other, attr)
            equal = isinstance(oattr, type(sattr))
            if not equal:
                my_logger.debug(
                    f"{attr}  have different types: self:{type(sattr)} other:{type(oattr)}"
                )
                return equal
            if attr in [
                "rng",
                "cov",
                "mean",
            ]:  # attributes we need to do something special for
                continue
            equal = sattr == oattr
            if not equal:
                my_logger.debug(f"{attr}  differ: self:{sattr} other:{oattr}")
                return equal

        equal = self.rng.__getstate__() == other.rng.__getstate__()
        if not equal:
            my_logger.debug("rng states  differ")
            return equal

        equal = self.cov.equals(other.cov)
        if not equal:
            my_logger.debug(f"cov differ self:{self.cov} other:{other.cov}")
            return equal

        equal = self.mean.equals(other.mean)
        if not equal:
            my_logger.debug(f"mean differ self:{self.mean} other:{other.mean}")
            return equal

        return equal

    def to_dict(self) -> dict:
        """
        Convert a provisional object to a dict. For the RNG generator, the state is used.
        Used for generic_json.dump/dumps
        :return:dct
        """
        result = super().to_dict()
        result["rng"] = self.rng.__getstate__()  # get the state of the RNG.
        return result

    @classmethod
    def from_dict(cls, dct: dict) -> "provisional":
        """
        Convert a dict into a provisional object. Used for generic_json.load/loads
        :param dct: Information about the provisional object.
         rng is setup with the state stored here.  All other keys get set by the superclass from_dict class method
        :return: A provisional object.
        """
        rng_state = dct.pop("rng")
        rng = np.random.default_rng()
        rng.__setstate__(rng_state)
        self = super().from_dict(dct)  # use the super class
        self.rng = rng
        return self

    def prov_obs(self, key: str) -> typing.Optional[pd.Series]:
        """
        Provisionally register a key & simulated obs
          If key exists then the count for that key is incremented.

         :return: series of simulated (random) obs. OR  None iff have too many keys before registration
        """

        if key not in self.provisional_models:
            if len(self.provisional_models) >= self.max_provisional_cases:
                return None  # can't create a model

            self.provisional_models[key] = 0
            self.provisional_count += 1  # increase the count of provisional cases.
            my_logger.debug(
                f"Adding {key} to preliminary case list. Len now {len(self.provisional_models)}"
            )
        self.provisional_models[key] += 1
        return self.random_obs()

    def random_obs(self) -> pd.Series:
        """
        Generate a random observation.
        :return:A random sample from multi-variate gaussian distribution using self.mean and self.cov.
        """

        # verify mean and cov are compatible
        cov = self.cov
        mean = self.mean

        # generate a sample and wrap it into a pandas Series.
        result = pd.Series(
            self.rng.multivariate_normal(mean, cov, check_valid="raise"),
            index=mean.index,
        )

        return result


class runSubmit(SubmitStudy):
    provisional: typing.Optional[provisional]  # for provisional running

    """
    Class   to deal with running various algorithms. (not all of which are optimization).
      It is a specialisation of SubmitStudy and is separated out to make maintenance easier.
      Idea is that each method should use cases that exist and deal with parallelism.
      If it finds out that cases are missing it should raise optclim_exceptions.runModelError.
      Functions should  return a finalConfig which contains
      whatever additional information they consider useful.
    
       To add a new algorithm then either add it as a method in here or subclass this and add your own runMethods.
       runJacobian is fairly simple and might form a good model for this. See runAlgorithm.py which is the script that
       runs the whole system.
       
       has the following attributes over those in SubmitStudy
       provisional: provisional object which supports provisional generation. 

    """

    def __init__(self, *args, **kwargs):
        """
         Has same arguments and keyword arguments as superclass __init__ (SubmitStudy.__init__) with some additional:
          Retrieves max_provisional_cases and rng_seed from provisional info in the config and
          uses those to initialize a provisional class stored in provisional. If max_provisional_cases is None then
           no provisional calculations are done.
        :param scale -- If True apply scaling to obs.
        :param args: Positional arguments.
        :param kwargs: Keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.provisional = None
        prov_info = self.config.provisional_info()
        max_provisional_cases = prov_info.get("max_provisional_cases", None)
        rng_seed = prov_info.get("rng_seed", 1234567)

        if max_provisional_cases is not None:
            # set up mean and cov for random obs generation.

            mean = self.config.targets()
            cov = self.config.Covariances()["CovTotal"] * 10
            self.provisional = provisional(
                seed=rng_seed,
                max_provisional_cases=max_provisional_cases,
                mean=mean,
                cov=cov,
            )
            # temp hack for figuring out problems.
            self.provisional_params = []
            self.provisional_obs = []

    def sim_obs(self, params: dict, scale: bool = False) -> pd.Series:
        """
        Get simulated observations for observations we want. Will create a new model if needed.
        Handles preliminary cases.
        :param params: dictionary of parameters.
        :param scale: Scale observtions by the scalings in the config data.
        :return:
        """

        obs_names = self.config.obsNames()  # observations we want for this study
        model = self.get_model(parameters=params)
        create_model = False  # True if we want to create a new model.
        key = self.key(params)

        if ( model is not None  ) and model.is_processed():  # Model exists and is processed
            my_logger.debug(f"Model {model} exists")
            simulated_obs = model.simulated_obs.reindex(index=obs_names)
            missing_obs = set(obs_names) - set(simulated_obs.index)
            if len(missing_obs) > 0:  # trigger error as missing obs
                raise ValueError(
                    f"Missing {' '.join(missing_obs)} from model {model.name}"
                )
            if np.any(simulated_obs.isnull()):  # check for missing.
                raise ValueError(
                    f"simulated_obs missing some obs {simulated_obs[simulated_obs.isnull]}"
                )

        elif (model is not None) and model.is_created():  # model exists and is created
            my_logger.warning(f"Asking for created model: {model}")
            create_model = False
            if self.provisional is None:
                simulated_obs = pd.Series( np.nan, index=obs_names )  # empty simulated obs
            else:
                simulated_obs = self.provisional.random_obs()  # random sim obs.

        elif model is not None:  # model exists but is neither processed or created
            raise ValueError(f"Model {model} in unexpected state")

        elif self.provisional is not None:  # provisional case
            create_model = self.provisional.create_models and (key in self.provisional.provisional_models)
            simulated_obs = self.provisional.prov_obs( key )  # try and register the key getting back random obs.
            if simulated_obs is None:  # made enough provisional cases
                my_logger.debug("Made enough provisional cases.")
                raise optclim_exceptions.enoughProvisionalCases(
                    "sim_obs had enough provisional obs"
                )  # tell control level to do proper generation!

        else:  # "normal" case (no provisional running)  -- return series of nan
            simulated_obs = pd.Series(np.nan, index=obs_names)  # empty simulated obs
            create_model = True
            my_logger.debug(f"Creating nan obs for key: {key}")

        if create_model:  # want to create a model?
            model = self.create_model(params, dump=False)
            # do not dump. Let the instantiate step handle that.
            if model is None:  # failed to create a new model
                # Time to go and create what we have.
                logging.debug(f"Did not create model for {params}")
                raise optclim_exceptions.submitModel("sim_obs made enough models")
            if self.provisional is not None:
                self.provisional.successful_provisional_count += (
                    1  # Successful use of provisional.
                )

        if scale:  # scale sim obs.
            simulated_obs *= self.config.scales()
        return simulated_obs

    def stdFunction(
        self,
        params: np.ndarray,
        df: bool = False,
        raiseError: bool = True,
        ensemble_average: bool = True,
        transform: typing.Optional[pd.DataFrame] = None,
        scale: bool = False,
        residual: bool = False,
        sumSquare: bool = False,
    ) -> np.ndarray | pd.DataFrame | pd.Series:
        """
        Standard Function used for running model . Returns values from cache if already got it.
          If not got cached model then raises runModelError exception or returns NaN
           after generating new model.


        It is a method rather than a class function as it makes use of some information in the object.
        So to actually get used as a function it needs to be converted to a function. This is done by genOptFunction which
          uses partial.function to do it. But this function does all the work.

        :param params -- a numpy array with the parameter values.
                These parameters should  be ordered as in self.paramNames()
        :param df  -- If True return all obs (read in after processing) as a dataframe.
        :param raiseError  -- If True raise optclim_exceptions.runModelError  if any requested models do not exist.
                This should cause generation & submission of models that need running.
                Else return array full of nans.
        :param ensemble_average -- If True average the ensemble members.

        The four  parameters below they are applied in the order: scale, residual, transform, sumSquare
        :param scale   -- if True scale obs  by self.scales()

        :param residual  -- if True remove target (self.target()) from obs

        :param transform  -- if provided transform the model obs by matrix multiplying them by this matrix.
                  It should be  N*nobs where nobs are the number of sim ons and  0 <= N <= nobs.
                  One application of this is to transform the data into a basis of eigenvectors of an
                    error covariance matrix. The transform matrix should be provided as a pandas datarray.
                  Column names being the obs names. Index being sensible labels for rows which will be new obs names.
                  Do be careful to make sure transform has same scaling as here...

        :param sumSquare (defaultFalse) -- if True return the sum of squares of the observations after any processing.


        Using stdFunction -- this  is a method as it needs to know various bits of information contained in ModelSubmit..
        To actually use it with optimisation function you need to call runSubmit.genOptFunction(**kwargs).
        That will give you a function suitable for many optimisation functions.  If you have more complex needs you
          likely need to add the runSubmit object to the list of arguments and then do runSubmit.stdFunction(.... )
          This might require you to create  a partial function. Your life will probably be easier if you set df=True
          and work with dataframes in your function.
        """

        paramNames = self.config.paramNames()
        # params can be a 2D array...
        if params.ndim == 1:
            use_params = params.reshape(1, -1)

        elif params.ndim == 2:
            use_params = params

        else:
            raise Exception("params should be 1 or 2d ")
        nsim = use_params.shape[
            0
        ]  # no of simulations we want to do which is the no of parameter sets provided
        nparams = use_params.shape[
            1
        ]  # no of params only used to check that parameter array is as expected.
        if nparams != len(paramNames):
            raise ValueError(
                "No of parameters %i not consistent with no of varying parameters %i\n"
                % (nparams, len(paramNames))
                + "Params: "
                + repr(use_params)
                + "\n paramNames: "
                + repr(paramNames)
            )

        # column names affected by transform so use that if provided
        if transform is not None:
            if len(transform.index) == 0:
                print("Transform is \n", transform)
                raise ValueError(
                    "Transform has 0 len index. Fix your covariance. Exiting"
                )
            obsNames = transform.index
        else:
            obsNames = self.config.obsNames()
        nObs = len(obsNames)  # How many observations are we expecting?
        if nObs == 0:  # Got zero. Something gone wrong
            raise ValueError("No observations found. Check your configuration file ")
        # result = np.full((nsim, nObs), np.nan)  # array of np.nan for result
        result = (
            []
        )  # empty list. Will fill with series from analysis and then make into a dataframe.
        nEns = (
            self.config.ensembleSize()
        )  # how many ensemble members do we want to run.
        # empty = pd.Series(np.repeat(np.nan, nObs), index=obsNames)
        for indx in range(0, nsim):  # iterate over the simulations.
            pDict = dict(
                zip(paramNames, use_params[indx, :])
            )  # create dict with names and values.
            pDict.update(self.config.fixedParams())
            ensObs = []
            for ensembleMember in range(0, nEns):
                pDict.update(ensembleMember=ensembleMember)
                obs = self.sim_obs(pDict, scale=scale)
                if residual:  # difference from target obs
                    tgt = self.config.targets(scale=scale)
                    obs -= tgt
                if transform is not None:  # apply transform if required.
                    obs = (
                        obs @ transform.T
                    )  # obs in nsim x nobs; transform  is nev x nobs.
                ensObs.append(obs)
            # end of loop over ensemble members.

            # compute ensemble-mean if needed
            if ensemble_average and (len(ensObs) > 1):
                my_logger.debug("Computing ensemble average")
                ensObs = pd.DataFrame(ensObs)
                ensObs = [ensObs.mean(axis=0)]

            result.extend(
                ensObs.copy()
            )  # add to list of  results. Note copying as  ensObs is changed in the loop
        # end of loop over simulations to be done.

        result = pd.DataFrame(result)  # convert to a dataframe

        if raiseError and np.any(result.isnull()):
            # want to raise error if any result is nan.
            raise optclim_exceptions.submitModel("Have nan in results -- stdFunction")
        if sumSquare:
            result = (result**2).sum(axis=1)
        if not df:  # want it as values not  a dataframe
            result = np.squeeze(result.values)

        return result

    def run_function(
        self, function: typing.Callable, extra_errors: typing.Optional[list] = None
    ) -> typing.Any:
        """
        Run  function within try/except cases.
        Two levels -- outer level which catches  enoughProvisionalCases & submitModel errors,
                   -- inner which only catches enoughProvisionalCases
        :param function -- function to be run. This function should take no arguments and run the optimisation.
          This function  *must* be deterministic. If the function uses random numbers then it should initialise
          the RNG as part of the  function definition.
        :param extra_errors -- If provided then should be a list of extra errors to catch when the function is run for "real"

        Code will run function twice if self.provisional is not None.
                If so the first time function is run it will catch optclim_exceptions.enoughProvisionalCases.
                The second time it runs then it will, in addition, catch optclim_exceptions.submitModel plus extra_errors.
                If those errors are caught then submitModel will be raised which should trigger instantiation and submission of models
        :return results of function call
        """
        errors = [
            optclim_exceptions.submitModel,
            optclim_exceptions.enoughProvisionalCases,
        ]
        if extra_errors is not None:
            errors.extend(extra_errors)
        errors = tuple(errors)
        if self.provisional is not None:  # Generating provisional cases?
            self.provisional.set_for_provisional()
            try:  # capture enoughProvisionalCases
                solution0 = function()  # run the function.
                my_logger.debug(f"Terminated on preliminary running")
            except optclim_exceptions.enoughProvisionalCases:
                my_logger.debug(f"Generated enough preliminary cases")
            # end of dealing with provisional running.
            my_logger.debug(
                f"Generated {len(self.provisional.provisional_models)} provisional cases"
            )
        # Now run the full case which generates models.
        try:
            if isinstance(self.provisional, provisional):
                self.provisional.set_for_create()
            solution = function()
            if isinstance(self.provisional, provisional):
                self.provisional.set_for_provisional()
            # generate the actual solutions and cases to run.
            # This could raise optclim_exceptions.submitModel or enoughProvisionalCases
            if self.status().isin(["CREATED"]).any():
                # created some models which need submission.
                my_logger.debug(f"Have models to submit {self}")
                raise optclim_exceptions.submitModel(
                    "Have CREATED Models -- in run_function"
                )
                # force model submission.
        except errors:  # catch errors -- which will then raise submitModel to do submission of models.
            my_logger.debug(f"Submitting models {self}")
            if isinstance(self.provisional, provisional):
                self.provisional.set_for_provisional()
            raise optclim_exceptions.submitModel(
                "Models needs submission  -- in runFunc"
            )

        my_logger.debug(f"Completed algorithm and returning solution")
        return solution

    def genOptFunction(self, **kwargs):
        """

        :return:A function suitable for use in an optimisation  algorithm (or anything else that uses the framework).
        by generating a partial function which converts stdFunction from a method to a function in the argument list
        and any adds extra args (as named arguments) that are present. You can use this to give you something suitable
        for most optimisation methods or use your own function to wrap stdFunction.


        """

        fn = functools.partial(self.stdFunction, **kwargs)

        return fn

    def runOptimized(self):
        """
        :arg self
        Run optimised case using reference model which may be potentially different from configuration used to optimize.
        Cares about number of ensemble members (default is 1 if not set) and the optimum parameters which should be set.
        """

        # basic idea is that users takes final json file and edits it to suit their needs
        # a better approach might be to pass in the optimum values and a list of configurations to loop over
        # as use case is running optimised configurations in different reference cases.
        # this would be quite a long way from usual framing and might need a bit of engineering.
        # Want to benefit from Submits caching. But Submit assumes config defined at creation..
        # but need to embed it along with each model... That could be done through adding an option to
        # stdfunction to include a configuration overriding whatever is in the Submit method.
        # Then this code would loop over those configurations calling stdFunction with them.
        # Turn of raising error in stdFunction and then check for null in output. If so raise runModelError.
        # Also need to modify fixedParams (which are no longer fixed) or at least are overwritten
        # Actually coming to conclusion that this case is pushing Submit too far and better
        # to do raw model create and modification as already done. That script has access to
        # the final config (optimum param values, nEns) and n configurations.  Trick is
        # not running more than once... Which is what Submit gives...
        # Hard will come back when I actually have a need!
        start = self.config.optimumParams()
        modelFn = self.genOptFunction(df=True)
        def opt():
            return modelFn(start.values).squeeze()
        obsSeries = self.run_function(opt)#
        # should run ensemble avg etc as side effect...and if things don't exist raise modelError
        # now to set up information having done all cases because if we have got here modelError has not been raised.
        finalConfig = self.runConfig()  # get final runInfo
        finalConfig.beginParam(start)  # setup the begin values!
        finalConfig.optimumParams(None)
        finalConfig.best_obs(best_obs=obsSeries)
        return finalConfig

    def rangeAwarePerturbations(
        self, baseVals: pd.Series, parLimits: pd.DataFrame, steps: pd.Series
    ) -> pd.Series:
        """
        Generate perturb param values  towards the centre of the valid range for each
        parameter. Used in runJacobian

        Inputs

            arg: baseVals: parameters at which Jacobian computed at.
            arg: parLimits: dataset max,min limits. parLimits.loc[:,'maxParm'] are the max values;
              parLimits.loc[:,'minParam'] are the min values;
            arg: steps: stepsizes for each parameter.

        Inputs, except parLimits, are all pandas series
        Returns pandas series array defining the perturbed parameter values
        """

        my_logger.debug(f"in rangeAwarePerturbations with {baseVals}")
        # derive the centre-of-valid-range
        centres = (parLimits.loc["minParam", :] + parLimits.loc["maxParam", :]) * 0.5
        deltaParam = np.abs(steps)
        sgn = np.sign((centres - baseVals))
        L = sgn < 0
        deltaParam[L] *= -1

        return deltaParam

    def runJacobian(self, scale: bool = False):
        """
        Run Jacobian cases.
        Rather crude (first order accurate) estimate. Evaluate functions at
          optimum values +/- delta where delta is specified in the StudyConfig used to generate Submit
          +/- is to keep perturbations towards the centre pf the domain.
        A more accurate version would run 2nd order differences around the base point.
        But (as yet) no need for this so not implemented. Probably best done with a centred option.
        The Jacobian computed is the transformed Jacobian. (Apply Transpose matrix).


        :arg self -- a Submit object.
        :param scale -- If True apply scalings.
        :returns a configuration. The following methods should work on it:
                return: finalConfig -- a studyConfig. The following methods should give you useful data:

                finalConfig.transJacobian() -- the  transformed Jacobian matrix at the optimum pt
                finalConfig.hessian() -- the  hessian computed from J^T J at the optimum pt.

            runs runConfig to provide generic info. (See documentation for that)

        """

        configData = self.config
        Tmat = configData.transMatrix(scale=scale)
        modelFn = self.genOptFunction(
            raiseError=True, df=True, residual=True, transform=Tmat, scale=scale
        )
        def jacobian():
            base = configData.optimumParams()  # try with optimum parameters
            if base is None:  # if none go with the begin parameters.
                base = configData.beginParam()

            paramRanges = configData.paramRanges()
            steps = configData.steps()  # see what the steps are
            delta = self.rangeAwarePerturbations(
                base, paramRanges, steps
            )  # compute the actual deltas
            params = [
                base
            ]  # list of parameter values to run at -- including the base. Needed to compute jac

            for p, v in delta.items():  # iterate over parameters
                param = base[:].rename(p)
                param.loc[p] += delta.loc[p]
                params.append(param)

            params = pd.DataFrame(params)  # all the params together to maximize parallelism
            # check params all in range.
            if np.any(params > paramRanges.loc["maxParam", :]):
                print(
                    "Some parameters too large\n ",
                    paramRanges.loc["maxParam", :],
                    "\n",
                    params,
                )
                print(params > paramRanges.loc["maxParam", :])
                raise ValueError
            if np.any(params < paramRanges.loc["minParam", :]):
                print(
                    "Some parameters too small\n ",
                    paramRanges.loc["minParam", :],
                    "\n",
                    params,
                )
                print(params < paramRanges.loc["minParam", :])
                raise ValueError
            obs = modelFn(
                params.values
            )  # compute the obs where we need to. This may generate model simulations
            dobs = obs.iloc[1:, :] - obs.iloc[0, :]
            dobs = dobs.set_index(delta.index)
            jac = dobs.div(delta, axis=0)  # compute the Jacobian
            return jac

        jac = self.run_function(jacobian)
        finalConfig = self.runConfig()  # get the configuration
        finalConfig.transJacobian(transJacobian=jac)  # store the jacobian.
        hes = jac.T @ jac
        finalConfig.hessian(hes)
        return finalConfig

    def runDFOLS(self, scale=True):
        """
        run DFOLS algorithm. It runs until new models need to be ran or DFOLS complets.
                If new models are needed then runModelError will be raised.
                DFOLS raises np.linalg.linalg.LinAlgError if it tries to do calculations with nan (which is what it gets
                when model has not been ran). This then triggers a runModelError. The callee of this method
                should trap that error and then run the necessary models using Submit.submit.

        :param scale (default True). If True scale data for transform matrix and in calculations of obs
        See StudyConfig.scalings()
        It should not make any difference if scale is True or False. But if regularisation is done
        then it is better to have all (diagonal) elements of the covariance matrix have roughly the same mag
        which is really what scaling does for you. (for example converting kg/sec/m^2 to mm/day). Current implementation
        of tranMatrix truncates removing all eigenvectors and eigenvalues when egivenvalues < 1E-6 * max(evalues)

        return: finalConfig -- a studyConfig. The following methods should give you useful data:

                # Generic stuff (that is probably more useful)
                finalConfig.transJacobian() -- the final transformed Jacobian matrix at the optimum pt
                finalConfig.hessian() -- the final hessian computed from J^T J at the optimum pt.
                finalConfig.get_dataFrameInfo('diagnostic_info') -- Diagnostic info from DFOLS. See DFOLS documentation.
                finalConfig.optimumParams() -- optimum parameters.

            also can get generic info and cost info:
            runs runCost & runConfig to provide  info. (See documentation of those methods for what they provide)

        """
        import dfols
        import warnings
        import random

        configData = self.config
        varParamNames = configData.paramNames()
        dfols_config = configData.DFOLS_config()
        start = configData.beginParam()
        # Sensible defaults  for DFOLS -- which can be overwritten by config file
        userParams = {
            "logging.save_diagnostic_info": True,
            "logging.save_xk": True,
            "noise.quit_on_noise_level": True,
            "general.check_objfun_for_overflow": False,
            "init.run_in_parallel": True,  # run in parallel
            "interpolation.throw_error_on_nans": True,  # make an error happen!
        }

        prange = configData.paramRanges(paramNames=varParamNames)
        prange = (prange.loc["minParam", :].values, prange.loc["maxParam", :].values)
        # update the user parameters from the configuration.
        userParams = configData.DFOLS_userParams(userParams=userParams)
        tMat = configData.transMatrix(
            scale=scale
        )  # scaling on transform matrix and in optfn  needs to be the same.
        optFn = self.genOptFunction(
            transform=tMat, residual=True, raiseError=True, scale=scale
        )

        warnings.filterwarnings("ignore")  # Ignore all warnings..

        def run_dfols():  # function for running dfols.
            np.random.seed(123456)  # set seed
            result = dfols.solve(
                optFn,
                start.values,
                do_logging=False,
                objfun_has_noise=True,
                bounds=prange,
                scaling_within_bounds=True,
                maxfun=dfols_config.get("maxfun", 100),
                rhobeg=dfols_config.get("rhobeg", 1e-1),
                rhoend=dfols_config.get("rhoend", 1e-3),
                user_params=userParams,
            )
            return result

        solution = self.run_function(
            run_dfols, extra_errors=[np.linalg.linalg.LinAlgError]
        )

        # code here will be run when DFOLS has completed. It mostly is to put stuff in the final JSON file
        # so can easily be looked at for subsequent analysis.
        if solution.flag not in (solution.EXIT_SUCCESS, solution.EXIT_MAXFUN_WARNING):
            print(
                "dfols failed with flag %i error : %s" % (solution.flag, solution.msg)
            )
            raise Exception("Problem with dfols")

        # need to wrap best sol and put in other information into the final results file.
        filename = self.rootDir / (
            self.config.fileName().stem + "_final.json"
        )  # final confio
        finalConfig = self.runConfig(
            scale=scale, add_cost=True, filename=filename
        )  # get final runInfo
        best = pd.Series(solution.x, index=varParamNames).rename(finalConfig.name())
        # Generic stuff (that is probably more useful). Probably need the Jacobian in "normal" space...
        # But can transform model jacobian into smaller evect space.
        jacobian = solution.jacobian
        jacobian = pd.DataFrame(jacobian, columns=varParamNames, index=tMat.index)
        finalConfig.transJacobian(jacobian)
        hessian = jacobian @ jacobian.T
        finalConfig.hessian(hessian)

        finalConfig.optimumParams(optimum=best)  # store the optimum params.
        # need to put in the best case -- which may not be the best evaluation as DFOLS ignores "burn in"
        solution.diagnostic_info.index = range(0, solution.diagnostic_info.shape[0])
        finalConfig.dfols_solution(solution=solution)
        # finalConfig.setv('DFOLS_soln',vars(solution))
        print(f"DFOLS completed: Solution status: {solution.msg}")
        finalConfig.save()
        return finalConfig

    def runGaussNewton(self, verbose=False, scale=True):
        """

        param: verbose if True produce more verbose output.
        param: scale if True apply scaling (default is True)
        Run Gauss Newton algorithm as used in Tett et al, 2017.
        return: finalConfig -- a studyConfig. The following methods should give you useful data:
                finalConfig.GNparams() -- the best parameters (min error)
                finalConfig.GNcost() -- the cost for each function evaluation
                finalConfig.GNalpha() -- the values of alpha used for next optimisation in the linesearch
                finalConfig.GNhessian() -- the diagnosed Hessians (in transformed space) at each iteration
                # Generic stuff (that is probably more useful)
                finalConfig.transJacobian() -- the final transformed Jacobian matrix at the optimum pt
                finalConfig.hessian() -- the final hessian computed from J^T J at the optimum pt.
                finalConfig.optimumParams() -- optimum parameters.

            also can get generic info and cost info:
            as runs runCost & runConfig to provide  info. (See documentation of those methods for what they provide)

            As the Gauss-Newton  component of this algorithm is a deterministic  perturbation to the minima over a small
               number of line-search values then provisional running  is not reliable. To make it work provisional running
               needs modification to run several times (more than twice) and only go if have "hits" = number of times ran.

        """
        import Optimise
        # provisional running not supported for Gauss-Newton.  provisional running needs to be extended to
        # allow multiple final runs and only generate models if all hit!
        if self.provisional is not None:
            raise ValueError("provisional running not supported for runGaussNewton.")
        # extract internal covariance and transform it.

        configData = self.config
        optimise = configData.optimise().copy()  # get optimisation info
        intCov = configData.Covariances(trace=verbose, scale=scale)["CovIntVar"]
        # Scaling done for compatibility with optFunction.
        # need to transform intCov. errCov should be I after transform.
        tMat = configData.transMatrix(scale=scale)
        intCov = tMat.dot(intCov).dot(tMat.T)
        # This is correct-- it is the internal covariance transformed
        optimise["sigma"] = False  # wrapped optimisation into cost function.
        optimise["deterministicPerturb"] = True  # deterministic perturbations.
        paramNames = configData.paramNames()
        nObs = tMat.shape[
            0
        ]  # might be a smaller because some evals in the covariance matrix are close to zero (or -ve)
        start = configData.beginParam(paramNames=paramNames)
        optFn = self.genOptFunction(
            transform=tMat, scale=scale, residual=True, raiseError=True
        )

        def gauss_newton_fn():
            """
            Function to deterministically run Gauss Newton
            """
            np.random.seed(123456)  # set rng seed to same value

            result = Optimise.gaussNewton(
                optFn,
                start.values,
                configData.paramRanges(paramNames=paramNames).values.T,
                configData.steps(paramNames=paramNames).values,
                np.zeros(nObs),
                optimise,
                cov=np.identity(nObs),
                cov_iv=intCov,
                trace=verbose,
            )
            return result

        # FIXME. This is behaving strangely. With provisional running the code runs the jacobian calculation then the
        # first of the line-search evaluations. Which needs understanding.

        best, status, info = self.run_function(gauss_newton_fn)
        filename = self.rootDir / (
            self.config.fileName().stem + "_final.json"
        )  # final confio
        finalConfig = self.runConfig(
            scale=scale, add_cost=True, filename=filename
        )  # get final runInfo
        finalConfig.GNstatus(status)
        # Store the GN specific stuff. TODO consider removing these and just store the info.
        finalConfig.GNparams(info["bestParams"])
        finalConfig.GNcost(info["err_constraint"])
        finalConfig.GNalpha(info["alpha"])
        # finalConfig.GNjacobian(info['jacobian']) #FIXME -- this needs fixing as we are in the evect space
        finalConfig.GNhessian(info["hessian"])
        # Generic stuff (that is probably more useful)
        jacobian = pd.DataFrame(info["jacobian"][-1, :, :], index=paramNames)
        finalConfig.transJacobian(jacobian)
        hessian = jacobian @ jacobian.T
        finalConfig.hessian(hessian)

        best = pd.Series(
            best, index=finalConfig.paramNames(), name=finalConfig.name()
        )  # wrap best result as pandas series
        finalConfig.optimumParams(optimum=best)  # write the optimum params
        print("status", status)

        return finalConfig

    def runPYSOT(self, scale=True):

        """
        Run PYSOT algorithm

        Not been ran or tested. Likely needs various things set up to make it work..
        :params scale -- if True scale data internally by scalings.

        :returns finalConfig (a studyConfig)
            from which you can get generic info and cost info:
            See documentation of runCost & runConfig methods  to see what they provide.
        """

        # pySOT -- probably won't work without some work. conda instal conda-forge pysot will install it.
        import pySOT

        warnings.warn("No testing done for pysot")
        configData = self.config
        optimise = configData.optimise().copy()  # get optimisation info
        tMat = configData.transMatrix()
        optFn = self.genOptFunction(transform=tMat, residual=True)  # need scale??
        paramNames = self.paramNames()
        from pySOT.experimental_design import SymmetricLatinHypercube
        from pySOT.strategy import SRBFStrategy, DYCORSStrategy  # , SOPStrategy
        from pySOT.surrogate import (
            RBFInterpolant,
            CubicKernel,
            LinearTail,
            SurrogateUnitBox,
        )  # will not work anymore as SurrogateUnitBox not defined.
        from poap.controller import SerialController
        from pySOT.optimization_problems import OptimizationProblem

        # Wrapper written for pySOT 0.2.2 (installed from conda-forge)
        # written by Lindon Roberts
        # Based on
        # https://github.com/dme65/pySOT/blob/master/pySOT/examples/example_simple.py
        # Expect optimise parameters:
        #  - maxfun: total number of evaluations allowed, default 100
        #  - initial_npts: number of initial evaluations, default 2*n+1 where n is the number of variables to optimise
        pysot_config = optimise.get("pysot", {})

        # Light wrapper of objfun for pySOT framework
        class WrappedObjFun(OptimizationProblem):
            def __init__(self):
                self.lb = (
                    configData.paramRanges(paramNames=paramNames)
                    .loc["minParam", :]
                    .values
                )  # lower bounds
                self.ub = (
                    configData.paramRanges(paramNames=paramNames)
                    .loc["maxParam", :]
                    .values
                )  # upper bounds
                self.dim = len(self.lb)  # dimensionality
                self.info = "Wrapper to DFOLS cost function"  # info
                self.int_var = np.array([])  # integer variables
                self.cont_var = np.arange(self.dim)  # continuous variables
                self.dfols_residual_function = optFn

            def eval(self, x):
                # Return same cost function as DFO-LS gets
                residuals = self.dfols_residual_function(
                    x
                )  # i.e. if DFO-LS asked for the model cost at x, it would get the vector "residuals"
                dfols_cost = np.dot(
                    residuals, residuals
                )  # sum of squares (no constant in front) - matches DFO-LS internal cost function
                return dfols_cost

        data = WrappedObjFun()  # instantiate wrapped objective function

        # Initial design of points
        slhd = SymmetricLatinHypercube(
            dim=data.dim, num_pts=pysot_config.get("initial_npts", 2 * data.dim + 1)
        )

        # Choice of surrogate model (cubic RBF interpolant with a linear tail)
        rbf = SurrogateUnitBox(
            RBFInterpolant(
                dim=data.dim, kernel=CubicKernel(), tail=LinearTail(data.dim)
            ),
            lb=data.lb,
            ub=data.ub,
        )

        # Use the serial controller (uses only one thread), SRBF strategy to find new points
        controller = SerialController(data.eval)
        strategy = pysot_config.get("strategy", "SRBF")
        maxfun = pysot_config.get("maxfun", 100)
        if strategy == "SRBF":
            controller.strategy = SRBFStrategy(
                max_evals=maxfun, opt_prob=data, exp_design=slhd, surrogate=rbf
            )
        elif strategy == "DYCORS":
            controller.strategy = DYCORSStrategy(
                max_evals=maxfun, opt_prob=data, exp_design=slhd, surrogate=rbf
            )
        else:
            raise RuntimeError(
                "Unknown pySOT strategy: %s (expect SRBF or DYCORS)" % strategy
            )

        # Run the optimization
        result = controller.run()

        # code here will be run when PYSOT has completed. It is mostly is to put stuff in the final JSON file
        # Gather key outputs: optimal x, optimal objective value, number of objective evaluations used
        xmin = result.params[0]
        fmin = result.value
        nf = len(controller.fevals)

        # need to wrap best soln xmin.
        finalConfig = self.runCost(self.config, scale=scale)
        finalConfig = self.runConfig(Config, finalConfig)  # get final runInfo
        best = pd.Series(xmin, index=paramNames)
        finalConfig.optimumParams(**(best.to_dict()))  # write the optimum params
        print("PYSOT completed")
        return finalConfig
