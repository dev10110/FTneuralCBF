import os
import sys
import torch
import numpy as np
import argparse

sys.path.insert(1, os.path.abspath('..'))
sys.path.insert(1, os.path.abspath('.'))

from dynamics.fixed_wing import FixedWing
from trainer import config
from trainer.constraints_fw import constraints
from trainer.datagen import Dataset_with_Grad
from trainer.trainer import Trainer
from trainer.utils import Utils
from trainer.NNfuncgrad import CBF, alpha_param, NNController_new
from pytictoc import TicToc

# from sympy import symbols, Eq, solve

sys.path.insert(1, os.path.abspath('..'))

# import cProfile
# cProfile.run('foo()')


xg = torch.tensor([[150.0,
                    0.1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0]])

x0 = torch.tensor([[120.0,
                    0.1,
                    0.1,
                    0.4,
                    0.5,
                    0.2,
                    0.1,
                    0.5,
                    0.9]])

dt = 0.01
n_state = 9
m_control = 4

# us_in = input("Training with fault (1) or no fault (0):")
# fault = int(us_in)

nominal_params = config.FIXED_WING_PARAMS

init_add = 1  # int(input("init data add? (0 -> no, 1 -> yes): "))
print(init_add)

init_param = 1  # int(input("use previous weights? (0 -> no, 1 -> yes): "))
print(init_param)

train_u = 0  # int(input("Train only CBF (0) or both CBF and u (1): "))
print(train_u)

fault_control_index = 1

n_sample = 10000

t = TicToc()


def main(args):
    fault = args.fault
    for iter_NN in range(1):
        NN_layers = np.mod(iter_NN, 4)
        dynamics = FixedWing(x=x0, nominal_params=nominal_params, dt=dt, controller_dt=dt)
        util = Utils(n_state=n_state, m_control=m_control, j_const=2, dyn=dynamics, dt=dt, params=nominal_params,
                     fault=fault,
                     fault_control_index=fault_control_index)
        # nn_controller = NNController_new(n_state=n_state, m_control=m_control)
        cbf = CBF(dynamics, n_state=n_state, m_control=m_control, iter_NN=-1)
        # alpha = alpha_param(n_state=n_state)
        if init_param == 1:
            try:
                if fault == 0:
                    cbf.load_state_dict(torch.load('./good_data/data/FW_cbf_NN_weightsCBF.pth'))
                    # nn_controller.load_state_dict(torch.load('./good_data/data/FW_controller_NN_weights.pth'))
                    # alpha.load_state_dict(torch.load('./good_data/data/FW_alpha_NN_weights.pth'))
                    cbf.eval()
                    # nn_controller.eval()
                    # alpha.eval()
                else:
                    cbf.load_state_dict(torch.load('./good_data/data/FW_cbf_FT_weightsCBF.pth'))
                    # nn_controller.load_state_dict(torch.load('./good_data/data/FW_controller_FT_weights.pth'))
                    # alpha.load_state_dict(torch.load('./good_data/data/FW_alpha_FT_weights.pth'))
                    cbf.eval()
                    # nn_controller.eval()
                    # alpha.eval()
            except:
                print("No good data available")
                try:
                    if fault == 0:
                        cbf.load_state_dict(torch.load('./data/FW_cbf_NN_weightsCBF.pth'))
                        # nn_controller.load_state_dict(torch.load('./data/FW_controller_NN_weights.pth'))
                        # alpha.load_state_dict(torch.load('./data/FW_alpha_NN_weights.pth'))
                        cbf.eval()
                        # nn_controller.eval()
                        # alpha.eval()
                    else:
                        cbf.load_state_dict(torch.load('./data/FW_cbf_FT_weightsCBF.pth'))
                        # nn_controller.load_state_dict(torch.load('./data/FW_controller_FT_weights.pth'))
                        # alpha.load_state_dict(torch.load('./data/FW_alpha_FT_weights.pth'))
                        cbf.eval()
                        # nn_controller.eval()
                        # alpha.eval()
                except:
                    print("No pre-train data available")

        dataset = Dataset_with_Grad(n_state=n_state, m_control=m_control, train_u=train_u)
        trainer = Trainer(cbf, dataset, dynamics, n_state=n_state, m_control=m_control, j_const=2,
                          dt=dt, action_loss_weight=1, params=nominal_params,
                          fault=fault, gpu_id=0,
                          fault_control_index=fault_control_index)

        loss_np = 1.0

        safety_rate = 0.0
        goal_reached = 0.0

        sm, sl = dynamics.state_limits()

        safe_m, safe_l = dynamics.safe_limits(sm, sl)

        i_train = 0

        if train_u == 1:
            for i in range(int(config.TRAIN_STEPS / config.POLICY_UPDATE_INTERVAL)):
                t.tic()
                # t.tic()
                # print(i)
                # if np.mod(i, config.INIT_STATE_UPDATE) == 0 and i > 0:
                if init_add == 1:
                    init_states0 = util.x_bndr(safe_m, safe_l, n_sample)
                    init_states0 = init_states0.reshape(n_sample, n_state) + torch.normal(mean=(sm + sl) / 10,
                                                                                          std=torch.ones(n_state))
                else:
                    init_states0 = torch.tensor([]).reshape(0, n_state)

                init_states1 = util.x_samples(sm, sl, n_sample)
                # init_states1 = init_states1.reshape(n_sample, n_state) + torch.normal(mean=(sm + sl) / 10,
                #                                                                         std=torch.ones(n_state))
                init_states = torch.vstack((init_states0, init_states1))

                num_states = init_states.shape[0]

                # init_unn = nn_controller.forward(torch.tensor(init_states, dtype=torch.float32),
                #                                  torch.tensor(init_u_nominal, dtype=torch.float32))
                # init_unn = torch.tensor(init_unn).reshape(num_states, m_control)
                for j in range(num_states):
                    for k in range(n_state):
                        if init_states[j, k] < sl[k] * 0.5:
                            init_states[j, k] = sl[k].clone()
                        if init_states[j, k] > sm[k] * 2:
                            init_states[j, k] = sm[k].clone()

                dataset.add_data(init_states, torch.tensor([]).reshape(0, m_control),
                                 torch.tensor([]).reshape(0, m_control))

                is_safe = int(torch.sum(util.is_safe(init_states))) / num_states

                safety_rate = (safety_rate * i + is_safe) / (i + 1)

                loss_np, acc_np, loss_h_safe, loss_h_dang, loss_alpha, loss_deriv_safe, loss_deriv_dang, loss_deriv_mid = trainer.train_cbf_and_controller(
                    iter_NN=iter_NN)
                print(
                    'step, {}, loss, {:.3f}, safety rate, {:.3f}, goal reached, {:.3f}, acc, {}, '
                    'loss_h_safe, {:.3f}, loss_h_dang, {:.3f}, loss_alpha, {:.3f}, loss_deriv_safe, {:.3f}, '
                    'loss_deriv_dang, {:.3f}, loss_deriv_mid, {:.3f}, '
                    'time of exec, {:.3f}'.format(
                        i, loss_np, safety_rate, goal_reached, acc_np, loss_h_safe, loss_h_dang, loss_alpha,
                        loss_deriv_safe, loss_deriv_dang, loss_deriv_mid, t.tocvalue()))

                if fault == 0:
                    str_cbf = './data/FW_cbf_NN_weights{}.pth'.format(iter_NN)
                    torch.save(cbf.state_dict(), str_cbf)
                    str_controller = './data/FW_controller_NN_weights{}.pth'.format(iter_NN)
                    torch.save(nn_controller.state_dict(), str_controller)
                    str_alpha = './data/FW_alpha_NN_weights{}.pth'.format(iter_NN)
                    torch.save(alpha.state_dict(), str_alpha)
                else:
                    str_cbf = './data/FW_cbf_FT_weights{}.pth'.format(iter_NN)
                    torch.save(cbf.state_dict(), str_cbf)
                    str_controller = './data/FW_controller_FT_weights{}.pth'.format(iter_NN)
                    torch.save(nn_controller.state_dict(), str_controller)
                    str_alpha = './data/FW_alpha_FT_weights{}.pth'.format(iter_NN)
                    torch.save(alpha.state_dict(), str_alpha)
                if loss_np < 0.01:
                    if fault == 0:
                        str_cbf = './good_data/data/FW_cbf_NN_weights{}.pth'.format(iter_NN)
                        torch.save(cbf.state_dict(), str_cbf)
                        str_controller = './good_data/data/FW_controller_NN_weights{}.pth'.format(iter_NN)
                        torch.save(nn_controller.state_dict(), str_controller)
                        str_alpha = './good_data/data/FW_alpha_NN_weights{}.pth'.format(iter_NN)
                        torch.save(alpha.state_dict(), str_alpha)
                    else:
                        str_cbf = './good_data/data/FW_cbf_FT_weights{}.pth'.format(iter_NN)
                        torch.save(cbf.state_dict(), str_cbf)
                        str_controller = './good_data/data/FW_controller_FT_weights{}.pth'.format(iter_NN)
                        torch.save(nn_controller.state_dict(), str_controller)
                        str_alpha = './good_data/data/FW_alpha_FT_weights{}.pth'.format(iter_NN)
                        torch.save(alpha.state_dict(), str_alpha)
                if loss_np <= 0.003 and i > int(config.TRAIN_STEPS / config.POLICY_UPDATE_INTERVAL) / 5:
                    break
        else:
            for i in range(int(config.TRAIN_STEPS / config.POLICY_UPDATE_INTERVAL)):
                if init_add == 1:
                    init_states0 = util.x_samples(safe_m, safe_l, n_sample) + 0 * torch.randn(n_sample, n_state)
                else:
                    init_states0 = torch.tensor([]).reshape(0, n_state)

                init_states1 = util.x_samples(sm, sl, config.POLICY_UPDATE_INTERVAL)

                init_states = torch.vstack((init_states0, init_states1))

                num_states = init_states.shape[0]

                init_states = init_states.reshape(num_states, n_state)

                dataset.add_data(init_states,
                                 torch.tensor([]).reshape(0, m_control), torch.tensor([]).reshape(0, m_control))

                is_safe = int(torch.sum(util.is_safe(init_states))) / num_states

                # safety_rate = safety_rate * (1 - config.POLICY_UPDATE_INTERVAL / config.TRAIN_STEPS) + \
                #               is_safe * config.POLICY_UPDATE_INTERVAL / config.TRAIN_STEPS
                safety_rate = (i * safety_rate + is_safe) / (i + 1)

                # if np.mod(i, config.POLICY_UPDATE_INTERVAL) == 0 and i > 0:

                if loss_np < 0.01 or i_train >= i - 1:
                    i_train = int(config.TRAIN_STEPS / config.POLICY_UPDATE_INTERVAL) / 2 + 1
                else:
                    i_train = i
                loss_np, acc_np, loss_h_safe, loss_h_dang, loss_deriv_safe, loss_deriv_dang, loss_deriv_mid = trainer.train_cbf()
                print(
                    'step, {}, loss, {:.3f}, safety rate, {:.3f}, goal reached, {:.3f}, acc, {}, '
                    'loss_h_safe, {:.3f}, loss_h_dang, {:.3f}, loss_deriv_safe, {:.3f}, '
                    'loss_deriv_dang, {:.3f}, loss_deriv_mid, {:.3f}, '.format(
                        i, loss_np, safety_rate, goal_reached, acc_np, loss_h_safe, loss_h_dang,
                        loss_deriv_safe, loss_deriv_dang, loss_deriv_mid))
                if fault == 0:
                    torch.save(cbf.state_dict(), './data/FW_cbf_NN_weightsCBF.pth')
                    # torch.save(nn_controller.state_dict(), './data/FW_controller_NN_weights.pth')
                    # torch.save(alpha.state_dict(), './data/FW_alpha_NN_weightsCBF.pth')
                else:
                    torch.save(cbf.state_dict(), './data/FW_cbf_FT_weightsCBF.pth')
                    # torch.save(nn_controller.state_dict(), './data/FW_controller_FT_weights.pth')
                    # torch.save(alpha.state_dict(), './data/FW_alpha_FT_weightsCBF.pth')
                if loss_np < 0.01:
                    if fault == 0:
                        torch.save(cbf.state_dict(), './good_data/data/FW_cbf_NN_weightsCBF.pth')
                        # torch.save(nn_controller.state_dict(), './good_data/data/FW_controller_NN_weights.pth')
                        # torch.save(alpha.state_dict(), './good_data/data/FW_alpha_NN_weightsCBF.pth')
                    else:
                        torch.save(cbf.state_dict(), './good_data/data/FW_cbf_FT_weightsCBF.pth')
                        # torch.save(nn_controller.state_dict(), './good_data/data/FW_controller_FT_weights.pth')
                        # torch.save(alpha.state_dict(), './good_data/data/FW_alpha_FT_weightsCBF.pth')
                if loss_np < 0.001 and i > 100:
                    break


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-fault', type=int, default=0)
    args = parser.parse_args()
    main(args)
