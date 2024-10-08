# Forwarding/lib imports
import pyro.distributions
import torch.distributions.constraints
from . import kernels

# Logic imports
import pyro
import pyro.contrib.gp.kernels
import pyro.nn
from pyro.nn.module import PyroParam
import torch
import matplotlib.pyplot as plt


class GaussianProcess:
    """
    Gaussian Process class for fitting and evaluating parameters
    """
    def __init__(self, kernel: pyro.contrib.gp.kernels.Isotropy, optimizer: torch.optim.Optimizer):
        self.kernel = kernel
        """Kernel for the Gaussian Process"""
        self.optimizer = optimizer
        """Optimizer for the Gaussian Process"""

    def fit(self, X: torch.Tensor, y: torch.Tensor, loss_fn: torch.nn.Module, lr: float = 0.01, num_steps: int = 1000, priors: dict = {}, verbose: bool = False):
        """
        Fit the Gaussian Process model, saves the model and training values for later use if needed.

        :param torch.Tensor X: Input domain
        :param torch.Tensor y: Target values
        :param torch.nn.Module loss_fn: Loss function
        :param float lr: Learning rate
        :param int num_steps: Number of steps
        :param dict priors: Priors for the kernel parameters
        :param bool verbose: Print training information

        :return: None
        """
        pyro.clear_param_store()

        # initialise kernel
        kernel = self.kernel(input_dim = 1)

        # set priors
        for param, prior in priors.items():
            setattr(kernel, param, prior)

        # gaussian regression
        sgpr = pyro.contrib.gp.models.GPRegression(X, y, kernel, jitter=1.0e-5)
        optimizer = self.optimizer(sgpr.parameters(), lr=lr)

        if verbose:
            print(sgpr)
            print(optimizer)

        # check if closure is needed
        if optimizer.__class__.__name__ == "LBFGS":
            def closure():
                optimizer.zero_grad()
                loss = loss_fn(sgpr.model, sgpr.guide)
                loss.backward()
                return loss
        else:
            closure = None

        losses = []
        for i in range(num_steps):
            optimizer.zero_grad()
            loss = loss_fn(sgpr.model, sgpr.guide)
            loss.backward()
            optimizer.step(closure)

            losses.append(loss.item())
            if verbose and (i % 100 == 0 or i == num_steps-1):
                print(f"lengthscale: {sgpr.kernel.lengthscale.item()}")
        print("Final lengthscale values...")
        print(f"lengthscale: {sgpr.kernel.lengthscale.item()}")
        
        # save data vals for test and plotting
        self.X_true = X
        self.y_true = y
        
        with torch.no_grad():
            self.mean, self.cov = sgpr(X, full_cov=True, noiseless=False)
            self.std = self.cov.diag().sqrt()

        self.fit_gp = sgpr
        self.params = sgpr.parameters()

    def test_plot(self, plot_sd: bool = False):
        """
        Create a test plot of the fitted model on the training data
        
        :return: None
        """
        # check if fit_gp exists
        if not hasattr(self, "fit_gp"):
            raise AttributeError("Please fit the model first")
        
        # plot
        plt.plot(self.X_true, self.mean, zorder=1, c='k')
        if plot_sd:
            plt.plot(self.X_true, self.mean + 2*self.std, zorder=0, c='r')
            plt.plot(self.X_true, self.mean - 2*self.std, zorder=0, c='r')

        plt.plot(self.X_true, self.y_true, zorder=0, c='b')


def background_noise(time: torch.Tensor, bckgd: torch.Tensor, bckgd_length: torch.Tensor, M: int, verbose: bool = False) -> torch.Tensor:
    """
    Fit a background noise model to the data

    :param ndarray time: Time in hours
    :param ndarray bckgd: Background time-series data
    :param ndarray bckgd_length: Length of each background trace
    :param int M: Count of background regions

    :return: Standard deviation of the noise model, list of noise models
    """
    def noise_model(X: torch.Tensor, y: torch.Tensor) -> GaussianProcess:
        process = GaussianProcess(pyro.contrib.gp.kernels.RBF, torch.optim.LBFGS)
        priors = {
            "lengthscale": PyroParam(torch.tensor(7.1), constraint=torch.distributions.constraints.greater_than(0.0)),
        }

        process.fit(X, y, pyro.infer.Trace_ELBO().differentiable_loss, priors=priors, num_steps=100)

        return process


    std_tensor = torch.zeros(M)
    models = []

    for i in range(M):
        X = time[:bckgd_length[i]]
        y = bckgd[:bckgd_length[i],i,None]  
        y = y - torch.mean(y)

        # remove y-dim
        y = y.reshape(-1)
        
        m = noise_model(X, y)

        models.append(m)
        std_tensor[i] = torch.pow(m.fit_gp.kernel.variance, 0.5)

    std = torch.mean(std_tensor)

    if verbose:
        print("Background noise model:")
        print(f"Standard deviation: {std}")

    return std, models