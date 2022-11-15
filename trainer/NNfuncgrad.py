import itertools
from typing import Tuple, List, Optional
from collections import OrderedDict
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl
import numpy as np


class CBF(nn.Module):

    def __init__(self, dynamics, n_state, m_control, iter_NN=0, preprocess_func=None, fault_control_index=1, fault=0):
        super().__init__()
        self.n_state = n_state
        self.fault = fault
        self.m_control = m_control
        self.dynamics = dynamics
        self.preprocess_func = preprocess_func
        self.fault_control_index = fault_control_index

        self.n_dims_extended = self.n_state
        if iter_NN < 0:
            self.cbf_hidden_layers = 3
        else:
            self.cbf_hidden_layers = 2 + iter_NN
        self.cbf_hidden_size = 128

        # if np.mod(iter_NN, 2) == 0:
        #     self.cbf_hidden_size = 128
        #     self.cbf_hidden_layers = 2 + iter_NN
        # else:
        #     self.cbf_hidden_layers = 1 + iter_NN
        #     self.cbf_hidden_size = 256

        self.V_layers: OrderedDict[str, nn.Module] = OrderedDict()

        self.V_layers["input_linear"] = nn.Linear(
            self.n_dims_extended, self.cbf_hidden_size
        )
        self.V_layers["input_activation"] = nn.Tanh()
        for i in range(self.cbf_hidden_layers):
            self.V_layers[f"layer_{i}_linear"] = nn.Linear(
                self.cbf_hidden_size, self.cbf_hidden_size
            )
            if i < self.cbf_hidden_layers - 1:
                self.V_layers[f"layer_{i}_activation"] = nn.Tanh()
        self.V_layers["output_linear"] = nn.Linear(self.cbf_hidden_size, 1)
        self.V_nn = nn.Sequential(self.V_layers)

    def forward(self, state):
        """
        args:
            state (bs, n_state)
            obstacle (bs, k_obstacle, n_state)
        returns:
            h (bs, k_obstacle)
        """
        h, Jh = self.V_with_jacobian(state)
        HJH = torch.hstack((h.reshape(1, 1), Jh.reshape(1, self.n_state)))

        return HJH

    def V_with_jacobian(self, x: torch.Tensor):
        """Computes the CLBF value and its Jacobian
        args:
            x: bs x self.dynamics_model.n_dims the points at which to evaluate the CLBF
        returns:
            V: bs tensor of CLBF values
            JV: bs x 1 x self.dynamics_model.n_dims Jacobian of each row of V wrt x
        """
        x_norm = torch.unsqueeze(x, 2)  # (bs, n_state, 1)
        bs = x_norm.shape[0]
        x_norm = x_norm.reshape(bs, self.n_state)
        su, sl = self.dynamics.state_limits()
        safe_m, safe_l = self.dynamics.safe_limits(su, sl)

        device_id = x.get_device()
        if device_id >= 0:
            safe_m = safe_m.cuda(device_id)
            safe_l = safe_l.cuda(device_id)

        x_norm, x_range = self.normalize(x_norm, safe_m, safe_l)
        x_range = x_range.reshape(self.dynamics.n_dims)
        x_norm = x_norm.reshape(bs, self.n_state)

        JV = torch.zeros(
            (bs, self.dynamics.n_dims, self.dynamics.n_dims)).type_as(x)

        for dim in range(self.dynamics.n_dims):
            JV[:, dim, dim] = 1.0 / x_range[dim].type_as(x)

        V = x_norm
        for layer in self.V_nn:
            V = layer(V)

            if isinstance(layer, nn.Linear):
                JV = torch.matmul(layer.weight, JV)
            elif isinstance(layer, nn.Tanh):
                JV = torch.matmul(torch.diag_embed(1 - V ** 2), JV)
            elif isinstance(layer, nn.ReLU):
                JV = torch.matmul(torch.diag_embed(torch.sign(V)), JV)

        # x_er = (x.reshape(bs, self.n_state) - (safe_m + safe_l).reshape(1, self.n_state) / 2).reshape(bs, 1,
        #                                                                                               self.n_state)
        # V_pre = - 0.5 * torch.matmul(x_er,
        #                              x_er.reshape(bs, self.n_state, 1)) + torch.matmul(
        #     ((safe_m - safe_l) / 2).reshape(1, self.n_state),
        #                      ((safe_m - safe_l) / 2).reshape(self.n_state, 1))
        #
        # V_shape = V.shape
        # V = V + V_pre.reshape(V_shape)
        #
        # # JV_pre = torch.zeros(JV.shape)
        #
        # JV_pre = -x_er.reshape(bs, 1, self.n_state)
        #
        # JV = JV + JV_pre
        return V, JV

    def normalize(self, x: torch.Tensor, x_max, x_min):
        """Normalize the state input to [-k, k]

        args:
            dynamics_model: the dynamics model matching the provided states
            x: bs x self.dynamics_model.n_dims the points to normalize
            k: normalize non-angle dimensions to [-k, k]
        """
        bs = x.shape[0]
        # su, sl = self.dynamics.state_limits()
        # x_max, x_min = self.dynamics.safe_limits(su, sl)
        x_max = x_max.reshape(1, self.n_state, 1)
        x_min = x_min.reshape(1, self.n_state, 1)
        x_center = (x_max + x_min).type_as(x.clone().detach()) / 2
        x_center = x_center.reshape(1, self.n_state, 1)
        x_range = (x_max - x_min) / 2.0
        x_norm = x.reshape(bs, self.n_state, 1) - x_center  # .type_as(x) #.reshape(shape_x)
        x_range = x_range.reshape(1, self.n_state, 1)

        x_norm = x_norm / x_range.type_as(x)

        return x_norm, x_range


class alpha_param(nn.Module):

    def __init__(self, n_state, preprocess_func=None):
        super().__init__()
        self.n_state = n_state

        self.preprocess_func = preprocess_func

        self.conv0 = nn.Conv1d(n_state, 64, 1)
        self.conv1 = nn.Conv1d(64, 128, 1)
        self.conv2 = nn.Conv1d(128, 128, 1)
        self.conv3 = nn.Conv1d(128, 128, 1)
        self.conv4 = nn.Conv1d(128, 1, 1)
        self.activation = nn.ReLU()
        self.output_activation = nn.Tanh()

    def forward(self, state):
        """
        args:
            state (bs, n_state)
            obstacle (bs, k_obstacle, n_state)
        returns:
            h (bs, k_obstacle)
        """
        state = torch.unsqueeze(state, 2)  # (bs, n_state, 1)
        state_diff = state

        if self.preprocess_func is not None:
            state_diff = self.preprocess_func(state_diff)

        x = self.activation(self.conv0(state_diff))
        x = self.activation(self.conv1(x))
        x = self.activation(self.conv2(x))  # (bs, 128, k_obstacle)
        x = self.activation(self.conv3(x))
        x = self.conv4(x)
        alpha = torch.squeeze(x, dim=1)  # (bs, k_obstacle)
        return alpha


class NNController(nn.Module):

    def __init__(self, n_state, m_control, preprocess_func=None, output_scale=1.0):
        super().__init__()
        self.n_state = n_state
        self.k_obstacle = k_obstacle
        self.m_control = m_control
        self.preprocess_func = preprocess_func

        self.conv0 = nn.Conv1d(n_state, 64, 1)
        self.conv1 = nn.Conv1d(64, 128, 1)
        self.conv2 = nn.Conv1d(128, 128, 1)
        self.fc0 = nn.Linear(128 + m_control + n_state, 128)
        self.fc1 = nn.Linear(128, 64)
        self.fc2 = nn.Linear(64, m_control)
        self.activation = nn.ReLU()
        self.output_activation = nn.Tanh()
        self.output_scale = output_scale

    def forward(self, state, obstacle, u_nominal, state_error):
        """
        args:
            state (bs, n_state)
            obstacle (bs, k_obstacle, n_state)
            u_nominal (bs, m_control)
            state_error (bs, n_state)
        returns:
            u (bs, m_control)
        """
        state = torch.unsqueeze(state, 2)  # (bs, n_state, 1)
        # print(state)
        # print(len(state))
        obstacle = obstacle.permute(0, 2, 1)  # (bs, n_state, k_obstacle)
        state_diff = state - obstacle

        if self.preprocess_func is not None:
            state_diff = self.preprocess_func(state_diff)
            state_error = self.preprocess_func(state_error)

        x = self.activation(self.conv0(state_diff))
        x = self.activation(self.conv1(x))
        x = self.activation(self.conv2(x))  # (bs, 128, k_obstacle)
        x, _ = torch.max(x, dim=2)  # (bs, 128)
        x = torch.cat([x, u_nominal, state_error], dim=1)  # (bs, 128 + m_control)
        x = self.activation(self.fc0(x))
        x = self.activation(self.fc1(x))
        x = self.output_activation(self.fc2(x)) * self.output_scale
        u = x + u_nominal
        return u


class NNController_new(nn.Module):

    def __init__(self, n_state, m_control, preprocess_func=None, output_scale=1.0):
        super().__init__()
        self.n_state = n_state
        # self.k_obstacle = k_obstacle
        self.m_control = m_control
        self.preprocess_func = preprocess_func

        self.conv0 = nn.Conv1d(n_state, 64, 1)
        self.conv1 = nn.Conv1d(64, 128, 1)
        self.conv2 = nn.Conv1d(128, 128, 1)
        self.fc0 = nn.Linear(128 + m_control, 128)
        self.fc1 = nn.Linear(128, 64)
        self.fc2 = nn.Linear(64, m_control)
        self.activation = nn.ReLU()
        self.output_activation = nn.Tanh()
        self.output_scale = output_scale

    def forward(self, state, u_nominal):
        """
        args:
            state (bs, n_state)
            obstacle (bs, k_obstacle, n_state)
            u_nominal (bs, m_control)
            state_error (bs, n_state)
        returns:
            u (bs, m_control)
        """
        state = torch.unsqueeze(state, 2)  # (bs, n_state, 1)
        # print(state)
        # print(len(state))
        # obstacle = obstacle.permute(0, 2, 1) # (bs, n_state, k_obstacle)
        # state_diff = state - obstacle

        if self.preprocess_func is not None:
            state = self.preprocess_func(state)
            # state_error = self.preprocess_func(state_error)

        x = self.activation(self.conv0(state))
        x = self.activation(self.conv1(x))
        x = self.activation(self.conv2(x))  # (bs, 128, k_obstacle)
        x, _ = torch.max(x, dim=2)  # (bs, 128)
        x = torch.cat([x, u_nominal], dim=1)  # (bs, 128 + m_control)
        x = self.activation(self.fc0(x))
        x = self.activation(self.fc1(x))
        x = self.output_activation(self.fc2(x)) * self.output_scale
        u = x + u_nominal
        return u
