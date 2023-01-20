import os
import sys

import numpy
import torch
import math
import random
import numpy as np
import matplotlib.pyplot as plt
import tqdm

import seaborn as sns

sns.set_theme(context="paper", font_scale=3.0, style="ticks")

sys.path.insert(1, os.path.abspath(".."))
sys.path.insert(1, os.path.abspath("."))

# from dynamics.fixed_wing_dyn import fw_dyn_ext, fw_dyn
from dynamics.Crazyflie import CrazyFlies
from trainer import config
from trainer.constraints_crazy import constraints
from trainer.datagen import Dataset_with_Grad
from trainer.trainer import Trainer
from trainer.utils import Utils
from trainer.NNfuncgrad_CF import CBF, Gamma

xg = torch.tensor([0.0, 0.0, 5.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

x0 = torch.tensor([[2.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
dt = 0.001
n_state = 12
m_control = 4
fault = 0

traj_len = config.TRAJ_LEN

nominal_params = config.CRAZYFLIE_PARAMS

fault_control_index = 1
fault_duration = config.FAULT_DURATION

fault_known = 1

n_sample = 1

def main():
    dynamics = CrazyFlies(x=x0, goal=xg, nominal_params=nominal_params, dt=dt)
    util = Utils(
        n_state=n_state,
        m_control=m_control,
        dyn=dynamics,
        params=nominal_params,
        fault=fault,
        fault_control_index=fault_control_index,
        j_const=2,
    )

    # NN_controller = NNController_new(n_state=n_state, m_control=m_control)
    NN_cbf = CBF(dynamics, n_state=n_state, m_control=m_control)
    gamma = Gamma(n_state=n_state, m_control=m_control, traj_len=traj_len)
    # NN_alpha = alpha_param(n_state=n_state)
    FT_cbf = CBF(dynamics, n_state=n_state, m_control=m_control)
    NN_cbf.load_state_dict(
        torch.load(
            "./good_data/data/CF_cbf_NN_weightsCBF.pth",
            map_location=torch.device("cpu"),
        )
    )
    FT_cbf.load_state_dict(
        torch.load(
            "./good_data/data/CF_cbf_FT_weightsCBF.pth",
            map_location=torch.device("cpu"),
        )
    )
    try:
        # NN_controller.load_state_dict(torch.load('./good_data/data/CF_controller_NN_weightsCBF.pth'))
        
        gamma.load_state_dict(
            torch.load(
                "./good_data/data/CF_gamma_NN_weightssingle1.pth",
                map_location=torch.device("cpu"),
            )
        )
        # NN_alpha.load_state_dict(torch.load('./good_data/data/CF_alpha_NN_weights.pth'))
    except:
        # NN_controller.load_state_dict(torch.load('./data/CF_controller_NN_weights.pth'))
        
        gamma.load_state_dict(
            torch.load(
                "./data/CF_gamma_NN_weightssingle1.pth",
                map_location=torch.device("cpu"),
            )
        )
        # NN_alpha.load_state_dict(torch.load('./data/CF_alpha_NN_weights.pth'))

    NN_cbf.eval()

    FT_cbf.eval()

    gamma.eval()

    state = x0

    safety_rate = 0.0
    unsafety_rate = 0.0
    h_correct = 0.0
    dot_h_correct = 0.0
    epsilon = 0.1

    um, ul = dynamics.control_limits()
    um = um.reshape(1, m_control).repeat(1, 1)
    ul = ul.reshape(1, m_control).repeat(1, 1)

    um = um.type(torch.FloatTensor)
    ul = ul.type(torch.FloatTensor)

    sm, sl = dynamics.state_limits()

    state_traj = torch.tensor([]).reshape(n_sample, 0, n_state)
    
    state_traj_diff = state_traj.clone()

    u_traj = torch.tensor([]).reshape(n_sample, 0, m_control)

    x_pl = np.array(state).reshape(1, n_state)
    fault_activity = np.array([-1])
    actual_fault_index = np.array([-1])
    detect_activity = np.array([0])
    NN_fault_index = np.array([-1])

    u_pl = np.array([0] * m_control).reshape(1, m_control)
    h, _ = NN_cbf.V_with_jacobian(state.reshape(1, n_state, 1))

    h_pl = np.array(h.detach()).reshape(1, 1)

    rand_start = random.uniform(1.01, 50)

    fault_start_epoch = config.EVAL_STEPS / 2 # 10 * math.floor(config.EVAL_STEPS / rand_start)
    fault_start = 0
    detect = 0

    dot_h_pl = np.array([0])

    previous_state = state.clone()
    
    fault_index_NN = -1
    gamma_min = 1
    
    detect_start = -1

    detect_prev = 0

    for i in tqdm.trange(config.EVAL_STEPS):

        u_nominal = dynamics.u_nominal(state)

        u_command = u_nominal.clone()

        for j in range(n_state):
            if state[0, j] < sl[j]:
                state[0, j] = sl[j].clone()
            if state[0, j] > sm[j]:
                state[0, j] = sm[j].clone()

        fx = dynamics._f(state, params=nominal_params)
        gx = dynamics._g(state, params=nominal_params)
        if detect == 0:
            h, grad_h = NN_cbf.V_with_jacobian(state.reshape(1, n_state, 1))
            h_prev, _ = NN_cbf.V_with_jacobian(previous_state.reshape(1, n_state, 1))
            u = util.neural_controller(u_nominal, fx, gx, h, grad_h, detect)

            u = u.clone().type(torch.float32)

            u = u.reshape(1, m_control)

            u_command = u.clone()

            gxu_no_fault = torch.matmul(gx, u.reshape(m_control, 1))

            dx_no_fault = fx.reshape(1, n_state) + gxu_no_fault.reshape(1, n_state)

            if fault_start_epoch <= i <= fault_start_epoch + fault_duration:
                u[0, fault_control_index] = 0 * ul[0, 0].clone() #  0 * (torch.sin(torch.tensor(i / 100)) ** 2) * um[0, 0].clone()
                fault_start = 1.0
            else:
                fault_start = 0.0

            for j in range(m_control):
                if u[0, j] < ul[0, j]:
                    u[0, j] = ul[0, j].clone()
                if u[0, j] > um[0, j]:
                    u[0, j] = um[0, j].clone()

            u = u.clone().type(torch.float32)
            
            # u_command = u.clone()

            gxu = torch.matmul(gx, u.reshape(m_control, 1))

            dx = fx.reshape(1, n_state) + gxu.reshape(1, n_state)

            dot_h = (h - h_prev) / dt + 0.01 * h
        
        if i >= traj_len: # and np.mod(i, 50) == 0:
            gamma_NN = gamma(state_traj.reshape(n_sample, traj_len, n_state), state_traj_diff.reshape(n_sample, traj_len, n_state), u_traj.reshape(n_sample, traj_len, m_control))
            gamma_NN = gamma_NN.detach()
            gamma_min = torch.min(gamma_NN)
            fault_index_NN = torch.argmin(gamma_NN).numpy()
        else:
            gamma_min = 1
        # print(gamma_min)
        
        if gamma_min > 0.02:
            detect = 0
            fault_index_NN = -1
        else:
            if detect_start == -1:
                detect_start = i
        
        if (detect == 0 and gamma_min < 0.2 and i - detect_start > 75) or detect == 1:

            detect = 1

            h, grad_h = FT_cbf.V_with_jacobian(state.reshape(1, n_state, 1))

            u = util.neural_controller_gamma(u_nominal, fx, gx, h, grad_h, detect, fault_index_NN)

            u = u.reshape(1, m_control)

            u = torch.tensor(u, dtype=torch.float32)

            u_command = u.clone()

            for j in range(m_control):
                if u[0, j] <= ul[0, j]:
                    u[0, j] = ul[0, j].clone()
                if u[0, j] >= um[0, j]:
                    u[0, j] = um[0, j].clone()

            gxu_no_fault = torch.matmul(gx, u.reshape(m_control, 1))

            dx_no_fault = fx.reshape(1, n_state) + gxu_no_fault.reshape(1, n_state)

            if fault_start_epoch <= i <= fault_start_epoch + fault_duration:
                u[0, fault_control_index] = 0 * ul[0, 0].clone() #  0 * (torch.sin(torch.tensor(i / 100)) ** 2) * um[0, 0].clone() #  torch.rand(1) / 4

            gxu = torch.matmul(gx, u.reshape(m_control, 1))

            dx = fx.reshape(1, n_state) + gxu.reshape(1, n_state)
        # If we have previously detected a fault, switch to no fault if dot_h is
        # increasing

        detect_activity = np.vstack((detect_activity, detect))
        if fault_start == 1:
            actual_fault_index = np.vstack((actual_fault_index, fault_control_index))
        else:
            actual_fault_index = np.vstack((actual_fault_index, -1.0))

        if detect == 1:
            NN_fault_index = np.vstack((NN_fault_index, fault_index_NN))
        else:
            NN_fault_index = np.vstack((NN_fault_index, -1.0))

        if fault_known == 0:
            dot_h_pl = np.vstack((dot_h_pl, dot_h.clone().detach().numpy()))
        
        dot_h = util.doth_max_alpha(h, grad_h, fx, gx, um, ul)

        if fault_known == 1:
            dot_h_pl = np.vstack((dot_h_pl, dot_h.clone().detach().numpy()))

        if dot_h < 0:
            print(i)
        state_next = state + dx * dt

        state_next_no_fault = state + dx_no_fault * dt

        previous_state = state.clone()

        is_safe = int(util.is_safe(state))
        is_unsafe = int(util.is_unsafe(state))
        safety_rate += is_safe / config.EVAL_STEPS

        unsafety_rate += is_unsafe / config.EVAL_STEPS
        h_correct += (
            is_safe * int(h >= 0) / config.EVAL_STEPS
            + is_unsafe * int(h < 0) / config.EVAL_STEPS
        )
        dot_h_correct += torch.sign(dot_h.clone().detach()) / config.EVAL_STEPS

        x_pl = np.vstack((x_pl, np.array(state.clone().detach()).reshape(1, n_state)))
        fault_activity = np.vstack((fault_activity, fault_start))
        u_pl = np.vstack((u_pl, np.array(u.clone().detach()).reshape(1, m_control)))
        h_pl = np.vstack((h_pl, np.array(h.clone().detach()).reshape(1, 1)))

        state = state_next.clone()

        state_diff = state_next_no_fault - state
        
        state_traj = torch.cat([state_traj, state_next.reshape(1, 1, n_state)], dim=-2)
        state_traj = state_traj[:, -traj_len:, :]

        state_traj_diff = torch.cat([state_traj, state_diff.reshape(1, 1, n_state)], dim=-2)
        state_traj_diff = state_traj_diff[:, -traj_len:, :]

        u_traj = torch.cat([u_traj, u_command.reshape(1, 1, m_control)], dim=-2)
        u_traj = u_traj[:, -traj_len:, :]

        detect_prev = detect_activity[-1]
        # print('h, {}, dot_h, {}'.format(h.detach().cpu().numpy()[0][0], dot_h.detach().cpu().numpy()[0][0]))
    time_pl = np.arange(0.0, dt * config.EVAL_STEPS + dt, dt)
    
    fault_activity[-2] = 1.0
    fault_activity[-1] = 0.0

    z_pl = x_pl[:, 2]

    print(safety_rate)
    print(unsafety_rate)
    print(h_correct)
    print(dot_h_correct)

    u1 = u_pl[:, 0]
    u2 = u_pl[:, 1]
    u3 = u_pl[:, 2]
    u4 = u_pl[:, 3]

    colors = sns.color_palette()

    fig = plt.figure(figsize=(31, 9))
    axs = fig.subplots(1, 3)

    # Plot the altitude and CBF value on one axis
    z_ax = axs[0]
    z_ax.plot(time_pl, z_pl, linewidth=4.0, label="z (m)", color=colors[0])

    # z_ax.plot(time_pl, dot_h_pl, linewidth=2.0, label="z (m)", color=colors[2])

    unsafe_z = 1.0
    z_ax.plot(
        time_pl,
        0 * time_pl + unsafe_z,
        color="k",
        linestyle="--",
        linewidth=4.0,
    )
    z_ax.text(time_pl.max() * 0.05, unsafe_z + 0.1, "Unsafe boundary")
    z_ax.plot([], [], color=colors[1], linestyle="-", linewidth=4.0, label="CBF h(x)")
    z_ax.set_ylabel("Height (m)", color=colors[0])
    z_ax.set_xlabel("Time (s)")
    z_ax.set_xlim(time_pl[0], time_pl[-1])
    z_ax.tick_params(axis="y", labelcolor=colors[0])
    z_ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.17), ncol=2, frameon=False)

    h_ax = z_ax.twinx()
    h_ax.plot(time_pl, h_pl, linestyle="-", linewidth=4.0, color=colors[1])
    h_ax.plot(
        time_pl,
        0 * time_pl,
        color="k",
        linestyle="--",
        linewidth=4.0,
        label="Unsafe boundary",
    )
    h_ax.set_ylabel("CBF value", color=colors[1])
    h_ax.tick_params(axis="y", labelcolor=colors[1])

    # Plot the control action on another axis
    u_ax = axs[1]
    u_ax.plot(time_pl, u2, linewidth=2.0, label="$u_2$ (faulty)")
    u_ax.plot(time_pl, u1, linewidth=2.0, label="$u_1$")
    u_ax.plot(time_pl, u3, linewidth=2.0, label="$u_3$")
    u_ax.plot(time_pl, u4, linewidth=2.0, label="$u_4$")
    u_ax.set_xlabel("Time (s)")
    u_ax.set_ylabel("Control effort")
    u_ax.set_xlim(time_pl[0], time_pl[-1])
    u_ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.17),
        ncol=4,
        frameon=False,
        columnspacing=0.7,
        handlelength=0.7,
        handletextpad=0.3,
    )

    w_ax = axs[2]
    
    w_ax.plot(time_pl, actual_fault_index + 1.0, linewidth=4.0, label="Actual fault index")
    w_ax.plot(time_pl, NN_fault_index + 1.0, linewidth=4.0, label="Predicted fault index")
    w_ax.set_xlabel("Time (s)")
    w_ax.set_ylabel("Fault Index")
    w_ax.set_ylim(-0.5, 4.5)
    w_ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.17),
        ncol=3,
        frameon=False,
        # columnspacing=0.7,
        # handlelength=0.7,
        # handletextpad=0.3,
    )

    fig.tight_layout(pad=1.15)
    
    plt.savefig("./plots/plot_CF_gamma_single.png")

    
if __name__ == "__main__":
    main()
