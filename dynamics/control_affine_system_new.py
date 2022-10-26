"""Define an abstract base class for dynamical systems"""
from abc import (
    ABC,
    abstractmethod,
    abstractproperty,
)
from typing import Callable, Tuple, Optional, List

from matplotlib.axes import Axes
import numpy as np
import torch
from torch.autograd.functional import jacobian


# from neural_clbf.systems.utils import (
#     Scenario,
#     ScenarioList,
#     lqr,
#     robust_continuous_lyap,
#     continuous_lyap,
# )


class ControlAffineSystemNew(ABC):
    """
    Represents an abstract control-affine dynamical system.

    A control-affine dynamical system is one where the state derivatives are affine in
    the control input, e.g.:

        dx/dt = f(x) + g(x) u

    These can be used to represent a wide range of dynamical systems, and they have some
    useful properties when it comes to designing controllers.
    """

    def __init__(
            self,
            x: torch.Tensor,
            nominal_params,
            dt: float = 0.01,
            controller_dt: Optional[float] = None,
            use_linearized_controller: bool = True,
            scenarios: Optional = None,
    ):
        """
        Initialize a system.

        args:
            nominal_params: a dictionary giving the parameter values for the system
            dt: the timestep to use for simulation
            controller_dt: the timestep for the LQR discretization. Defaults to dt
            use_linearized_controller: if True, linearize the system model to derive a
                                       LQR controller. If false, the system is must
                                       set self.P itself to be a tensor n_dims x n_dims
                                       positive definite matrix.
            scenarios: an optional list of scenarios for robust control
        raises:
            ValueError if nominal_params are not valid for this system
        """
        super().__init__()
        # print(x)

        # Validate parameters, raise error if they're not valid
        if not self.validate_params(nominal_params):
            raise ValueError(f"Parameters not valid: {nominal_params}")

        self.nominal_params = nominal_params

        # Make sure the timestep is valid
        assert dt > 0.0
        self.dt = dt

        if controller_dt is None:
            controller_dt = self.dt
        self.controller_dt = controller_dt
        self.x = x

        # Compute the linearized controller

    @torch.enable_grad()
    @abstractmethod
    def validate_params(self, params) -> bool:
        """Check if a given set of parameters is valid

        args:
            params: a dictionary giving the parameter values for the system.
        returns:
            True if parameters are valid, False otherwise
        """
        pass

    @abstractproperty
    def n_dims(self) -> int:
        pass

    @abstractproperty
    def angle_dims(self) -> List[int]:
        pass

    @abstractproperty
    def n_controls(self) -> int:
        pass

    @abstractproperty
    def state_limits(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Return a tuple (upper, lower) describing the expected range of states for this
        system
        """
        pass

    @abstractproperty
    def control_limits(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Return a tuple (upper, lower) describing the range of allowable control
        limits for this system
        """
        pass

    @property
    def intervention_limits(self) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Return a tuple (upper, lower) describing the range of allowable changes to
        control for this system
        """
        upper_limit, lower_limit = self.control_limits

        return (upper_limit, lower_limit)

    def out_of_bounds_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating whether rows are outside the state limits
        for this system

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        upper_lim, lower_lim = self.state_limits
        out_of_bounds_mask = torch.zeros_like(x[:, 0], dtype=torch.bool)
        for i_dim in range(x.shape[-1]):
            out_of_bounds_mask.logical_or_(x[:, i_dim] >= upper_lim[i_dim])
            out_of_bounds_mask.logical_or_(x[:, i_dim] <= lower_lim[i_dim])

        return out_of_bounds_mask

    @abstractmethod
    def safe_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating safe regions for this system

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        pass

    @abstractmethod
    def unsafe_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating unsafe regions for this system

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        pass

    def failure(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating failure. This usually matches with the
        unsafe region

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        return self.unsafe_mask(x)

    def boundary_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating regions that are neither safe nor unsafe

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        return torch.logical_not(
            torch.logical_or(
                self.safe_mask(x),
                self.unsafe_mask(x),
            )
        )

    def goal_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Return the mask of x indicating goal regions for this system

        args:
            x: a tensor of (batch_size, self.n_dims) points in the state space
        returns:
            a tensor of (batch_size,) booleans indicating whether the corresponding
            point is in this region.
        """
        # Include a sensible default
        goal_tolerance = 0.1
        return (x - self.goal_point).norm(dim=-1) <= goal_tolerance

    @property
    def goal_point(self):
        # return torch.zeros((1, self.n_dims))
        return self.x

    @property
    def u_eq(self):
        return torch.zeros((1, self.n_controls))

    def sample_state_space(self, num_samples: int) -> torch.Tensor:
        """Sample uniformly from the state space"""
        x_max, x_min = self.state_limits

        # Sample uniformly from 0 to 1 and then shift and scale to match state limits
        x = torch.Tensor(num_samples, self.n_dims).uniform_(0.0, 1.0)
        for i in range(self.n_dims):
            x[:, i] = x[:, i] * (x_max[i] - x_min[i]) + x_min[i]

        return x

    def sample_with_mask(
            self,
            num_samples: int,
            mask_fn: Callable[[torch.Tensor], torch.Tensor],
            max_tries: int = 5000,
    ) -> torch.Tensor:
        """Sample num_samples so that mask_fn is True for all samples. Makes a
        best-effort attempt, but gives up after max_tries, so may return some points
        for which the mask is False, so watch out!
        """
        # Get a uniform sampling
        samples = self.sample_state_space(num_samples)

        # While the mask is violated, get violators and replace them
        # (give up after so many tries)
        for _ in range(max_tries):
            violations = torch.logical_not(mask_fn(samples))
            if not violations.any():
                break

            new_samples = int(violations.sum().item())
            samples[violations] = self.sample_state_space(new_samples)

        return samples

    def sample_safe(self, num_samples: int, max_tries: int = 5000) -> torch.Tensor:
        """Sample uniformly from the safe space. May return some points that are not
        safe, so watch out (only a best-effort sampling).
        """
        return self.sample_with_mask(num_samples, self.safe_mask, max_tries)

    def sample_unsafe(self, num_samples: int, max_tries: int = 5000) -> torch.Tensor:
        """Sample uniformly from the unsafe space. May return some points that are not
        unsafe, so watch out (only a best-effort sampling).
        """
        return self.sample_with_mask(num_samples, self.unsafe_mask, max_tries)

    def sample_goal(self, num_samples: int, max_tries: int = 5000) -> torch.Tensor:
        """Sample uniformly from the goal. May return some points that are not in the
        goal, so watch out (only a best-effort sampling).
        """
        return self.sample_with_mask(num_samples, self.goal_mask, max_tries)

    def sample_boundary(self, num_samples: int, max_tries: int = 5000) -> torch.Tensor:
        """Sample uniformly from the state space between the safe and unsafe regions.
        May return some points that are not in this region safe, so watch out (only a
        best-effort sampling).
        """
        return self.sample_with_mask(num_samples, self.boundary_mask, max_tries)

    def control_affine_dynamics(
            self, x: torch.Tensor, params
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Return a tuple (f, g) representing the system dynamics in control-affine form:

            dx/dt = f(x) + g(x) u

        args:
            x: bs x self.n_dims tensor of state
            params: a dictionary giving the parameter values for the system. If None,
                    default to the nominal parameters used at initialization
        returns:
            f: bs x self.n_dims x 1 tensor representing the control-independent dynamics
            g: bs x self.n_dims x self.n_controls tensor representing the control-
               dependent dynamics
        """
        # Sanity check on input
        assert x.ndim == 2
        assert x.shape[1] == self.n_dims

        # If no params required, use nominal params
        if params is None:
            params = self.nominal_params

        return self._f(x, params), self._g(x, params)

    def closed_loop_dynamics(
            self, x: torch.Tensor, u: torch.Tensor, params
    ) -> torch.Tensor:
        """
        Return the state derivatives at state x and control input u

            dx/dt = f(x) + g(x) u

        args:
            x: bs x self.n_dims tensor of state
            u: bs x self.n_controls tensor of controls
            params: a dictionary giving the parameter values for the system. If None,
                    default to the nominal parameters used at initialization
        returns:
            xdot: bs x self.n_dims tensor of time derivatives of x
        """
        # Get the control-affine dynamics
        # print(x.ndim)
        f, g = self.control_affine_dynamics(x, params=params)
        # print(f)

        # print(g)
        # Compute state derivatives using control-affine form
        xdot = f + torch.bmm(g, u.unsqueeze(-1))
        return xdot.view(x.shape)

    def zero_order_hold(
            self,
            x: torch.Tensor,
            u: torch.Tensor,
            controller_dt: float,
            params,
    ) -> torch.Tensor:
        """
        Simulate dynamics forward for controller_dt, simulating at self.dt, with control
        held constant at u, starting from x.

        args:
            x: bs x self.n_dims tensor of state
            u: bs x self.n_controls tensor of controls
            controller_dt: the amount of time to hold for
            params: a dictionary giving the parameter values for the system. If None,
                    default to the nominal parameters used at initialization
        returns:
            x_next: bs x self.n_dims tensor of next states
        """
        num_steps = int(controller_dt / self.dt)
        for tstep in range(0, num_steps):
            # Get the derivatives for this control input
            xdot = self.closed_loop_dynamics(x, u, params)

            # Simulate forward
            x = x + self.dt * xdot

        # Return the simulated state
        return x

    @abstractmethod
    def _f(self, x: torch.Tensor, params) -> torch.Tensor:
        """
        Return the control-independent part of the control-affine dynamics.

        args:
            x: bs x self.n_dims tensor of state
            params: a dictionary giving the parameter values for the system. If None,
                    default to the nominal parameters used at initialization
        returns:
            f: bs x self.n_dims x 1 tensor
        """
        pass

    @abstractmethod
    def _g(self, x: torch.Tensor, params) -> torch.Tensor:
        """
        Return the control-independent part of the control-affine dynamics.

        args:
            x: bs x self.n_dims tensor of state
            params: a dictionary giving the parameter values for the system. If None,
                    default to the nominal parameters used at initialization
        returns:
            g: bs x self.n_dims x self.n_controls tensor
        """
        pass

    def u_nominal(
            self, x: torch.Tensor, params
    ) -> torch.Tensor:
        """
        Compute the nominal control for the nominal parameters, using LQR unless
        overridden

        args:
            x: bs x self.n_dims tensor of state
            params: the model parameters used
        returns:
            u_nominal: bs x self.n_controls tensor of controls
        """
        # Compute nominal control from feedback + equilibrium control
        K = self.K.type_as(x)
        goal = self.goal_point.squeeze().type_as(x)
        u_nominal = -(K @ (x - goal).T).T

        # Adjust for the equilibrium setpoint
        u = u_nominal + self.u_eq.type_as(x)

        # Clamp given the control limits
        upper_u_lim, lower_u_lim = self.control_limits
        for dim_idx in range(self.n_controls):
            u[:, dim_idx] = torch.clamp(
                u[:, dim_idx],
                min=lower_u_lim[dim_idx].item(),
                max=upper_u_lim[dim_idx].item(),
            )

        return u

    def plot_environment(self, ax: Axes) -> None:
        """
        Add a plot of the environment to the given figure. Defaults to do nothing
        unless overidden.

        args:
            ax: the axis on which to plot
        """
        pass
