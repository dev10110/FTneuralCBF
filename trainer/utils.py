import pdb
import torch
import torch.distributions as td
import math
import scipy
import numpy as np
from qpsolvers import solve_qp
from osqp import OSQP
from scipy.sparse import identity
from scipy.sparse import vstack, csr_matrix, csc_matrix
from pytictoc import TicToc
from trainer.constraints_fw import LfLg_new

# from qpth.qp import QPFunction

t = TicToc()


class Utils(object):

    def __init__(self,
                 dyn,
                 params,
                 n_state,
                 m_control,
                 j_const=1,
                 dt=0.05,
                 fault=0,
                 fault_control_index=-1):

        self.params = params
        self.n_state = n_state
        self.m_control = m_control
        self.j_const = j_const
        self.dyn = dyn
        self.fault = fault
        self.fault_control_index = fault_control_index
        self.dt = dt

    def is_safe(self, state):

        # alpha = torch.abs(state[:,1])
        return self.dyn.safe_mask(state)

    def is_unsafe(self, state):

        # alpha = torch.abs(state[:,1])
        return self.dyn.unsafe_mask(state)

    def nominal_dynamics(self, state, u, batch_size):
        """
        args:
            state (n_state,)
            u (m_control,)
        returns:
            dsdt (n_state,)
        """

        m_control = self.m_control
        fx = self.dyn._f(state, self.params)
        gx = self.dyn._g(state, self.params)

        for j in range(self.m_control):
            if self.fault == 1 and self.fault_control_index == j:
                u[:, j] = u[:, j].clone().detach().reshape(batch_size, 1)
            else:
                u[:, j] = u[:, j].clone().detach().requires_grad_(True).reshape(batch_size, 1)

        dsdt = fx + torch.matmul(gx, u)

        return dsdt

    def nominal_controller(self, state, goal, u_n, dyn):
        """
        args:
            state (n_state,)
            goal (n_state,)
        returns:
            u_nominal (m_control,)
        """
        um, ul = self.dyn.control_limits()
        sm, sl = self.dyn.state_limits()

        n_state = self.n_state
        m_control = self.m_control
        params = self.params
        j_const = self.j_const

        batch_size = state.shape[0]

        size_Q = m_control + j_const

        Q = csc_matrix(identity(size_Q))
        Q[0, 0] = 1 / um[0]

        F = torch.ones(size_Q, 1)
        u_nominal = u_n
        for i in range(batch_size):
            # t.tic()
            state_i = state[i, :].reshape(1, n_state)
            F[0:m_control] = - u_n[i, :].reshape(m_control, 1)
            F = np.array(F)
            F[0] = F[0] / um[0]
            F[-1] = - 10
            fx = dyn._f(state_i, params)
            gx = dyn._g(state_i, params)

            fx = fx.reshape(n_state, 1)
            gx = gx.reshape(n_state, m_control)

            V, Lg, Lf = LfLg_new(state_i, goal, fx, gx, sm, sl)

            A = torch.hstack((- Lg, - V))
            B = Lf

            assert not A.is_cuda

            G = scipy.sparse.csc.csc_matrix(A)
            h = - np.array(B).reshape(j_const, 1)
            # u = scipy.optimize.linprog(F, A_ub=G, b_ub=h)

            u = solve_qp(Q, F, G, h, solver="osqp")

            # print(u)

            if u is None:
                u = u_n[i, :].reshape(1, m_control)
                # u = u.reshape(1,m_control)
            u = u[0:m_control]
            # print(u)
            u_nominal[i, :] = torch.tensor(u).reshape(1, m_control)
            # print(t.toc())
        return u_nominal

    def nominal_controller_batch(self, state, goal, u_n, dyn):
        """
        args:
            state (n_state,)
            goal (n_state,)
        returns:
            u_nominal (m_control,)
        """
        # state = state.cuda()
        # goal = torch.tensor(goal).cuda()
        # u_n = u_n.cuda()

        um, ul = self.dyn.control_limits()
        # um = torch.tensor(um).cuda()
        # ul = ul.cuda()
        n_state = self.n_state
        m_control = self.m_control
        params = self.params
        j_const = self.j_const

        batch_size = state.shape[0]

        # size_Q = (m_control + j_const) * batch_size

        # Q = csc_matrix(identity(size_Q))
        Q = torch.eye(m_control + j_const)  # .cuda()

        F = torch.ones(m_control + j_const, 1)  # .cuda()
        # A_l = torch.zeros(batch_size * j_const, size_Q)
        # B_l = torch.zeros(batch_size * j_const).reshape(batch_size * j_const, 1)
        u_n = u_n.reshape(batch_size, m_control)
        u_nominal = torch.zeros(batch_size, m_control)

        for i in range(batch_size):
            # t.tic()
            # print(i)
            # Q[m_control * i, m_control * i] = Q[m_control * i, m_control * i] / um[0]
            state_i = state[i, :].reshape(1, n_state)  # .cuda()
            F[0:m_control] = - u_n[i, :].reshape(m_control, 1)
            # F = np.array(F)
            F[0] = F[0] / um[0]
            F[-1] = - 10
            fx = dyn._f(state_i, params)
            gx = dyn._g(state_i, params)

            fx = fx.reshape(n_state, 1)
            gx = gx.reshape(n_state, m_control)

            V, Lg, Lf = LfLg_new(state_i, goal, fx, gx, 1, [np.pi / 8, -np.pi / 80])

            A = torch.hstack((- Lg, - V))

            # u = QPFunction(verbose=False)(Q, F.reshape(m_control+j_const), A, -Lf.reshape(j_const), torch.tensor([
            # ]).cuda(), torch.tensor([]).cuda())

            # A_l[i * j_const: (i+1) * j_const, i * (m_control + j_const): (i+1) * (m_control + j_const)] = A
            # # Lf = np.array(Lf)
            #
            # B_l[i * j_const: (i+1) * j_const] = Lf.reshape(j_const, 1)
            # G = A_l.cuda()  # scipy.sparse.csc.csc_matrix(A_l)
            # # F = np.array(F)
            # # h = - np.array(B_l)
            # h = - B_l.cuda()
            #
            u = solve_qp(Q, F, A, -Lf.reshape(j_const, 1), solver="osqp")
            # print(G.shape)
            # print(h.shape)
            #
            # # u = QPFunction(verbose=False)(Q, F, G, h, torch.tensor([]).cuda(), torch.tensor([]).cuda())
            #
            # print(u.shape)
            if u is None:
                u = u_n[i * m_control: (i + 1) * m_control].reshape(1, m_control)

            u = u.clone().cpu()
            u_nominal[i, :] = u[0][0:m_control].reshape(1, m_control)
        # for i in range(batch_size):
        #     u_nominal[i, :] = torch.tensor(u[i * m_control: (i + 1) * m_control]).reshape(1, m_control)
        # print(t.toc())

        return u_nominal

    def neural_controller(self, u_nominal, fx, gx, h, grad_h, fault_start):
        """
        args:
            state (n_state,)
            goal (n_state,)
        returns:
            u_nominal (m_control,)
        """
        um, ul = self.dyn.control_limits()
        m_control = self.m_control

        size_Q = m_control + 1

        Q = csc_matrix(identity(size_Q))
        # Q[0,0] = 1 / um[0]
        F = torch.hstack((torch.tensor(u_nominal).reshape(m_control), torch.tensor(1.0))).reshape(size_Q, 1)

        F = - np.array(F)
        # F[0] = F[0] / um[0]

        Q = Q / 100
        F = F / 100

        F[-1] = -1

        Lg = torch.matmul(grad_h, gx)
        Lf = torch.matmul(grad_h, fx)

        if fault_start == 1:
            Lf = Lf - torch.abs(Lg[0, 0, self.fault_control_index]) * um[self.fault_control_index]
            Lg[0, 0, self.fault_control_index] = 0

        if h == 0:
            h = 1e-4

        A = torch.hstack((- Lg.reshape(1, m_control), -h))
        A = torch.tensor(A.detach().cpu())
        B = Lf.detach().cpu().numpy()
        B = np.array(B)

        # print(A)
        A = scipy.sparse.csc.csc_matrix(A)
        u = solve_qp(Q, F, A, B, solver="osqp")

        if u is None:
            u_neural = u_nominal.reshape(m_control)
        else:
            u_neural = torch.tensor([u[0:self.m_control]]).reshape(1, m_control)

        return u_neural

    def x_bndr(self, sm, sl, N):
        """
        args:
            state lower limit sl
            state upper limit sm
        returns:
            samples on boundary x
        """

        n_dims = self.n_state
        batch = N

        normal_idx = torch.randint(0, n_dims, size=(batch,))
        assert normal_idx.shape == (batch,)

        # 2: Choose whether it takes the value of hi or lo.
        direction = torch.randint(2, size=(batch,), dtype=torch.bool)
        assert direction.shape == (batch,)

        lo = sl
        hi = sm
        assert lo.shape == hi.shape == (n_dims,)
        dist = td.Uniform(lo, hi)

        samples = dist.sample((batch,))
        assert samples.shape == (batch, n_dims)

        tmp = torch.where(direction, hi[normal_idx], lo[normal_idx])
        assert tmp.shape == (batch,)

        # print(tmp.shape)
        # tmp = 13 * torch.ones(batch)
        tmp = tmp[:, None].repeat(1, n_dims)

        # print("samples")
        # print(samples)
        # print("samples2")
        # print(samples[:, normal_idx])
        # print(samples[:, normal_idx].shape)

        # tmp2 = torch.arange(batch * n_dims).reshape((batch, n_dims)).float()

        # print(normal_idx)
        # print(samples.shape)

        # samples[:, normal_idx] = tmp
        samples.scatter_(1, normal_idx[:, None], tmp)

        # eq_lo = samples == sl
        # eq_hi = samples == sm
        #
        # n_on_bdry = torch.sum(eq_lo, dim=1) + torch.sum(eq_hi, dim=1)
        # all_on_bdry = torch.all(n_on_bdry >= 1)
        # print("all_on_bdry: ", all_on_bdry)

        return samples

    def x_samples(self, sm, sl, batch):
        """
        args:
            state lower limit sl
            state upper limit sm
        returns:
            samples on boundary x
        """

        n_dims = self.n_state

        normal_idx = torch.randint(0, n_dims, size=(batch,))
        assert normal_idx.shape == (batch,)

        # 2: Choose whether it takes the value of hi or lo.
        direction = torch.randint(2, size=(batch,), dtype=torch.bool)
        assert direction.shape == (batch,)

        lo = sl
        hi = sm
        assert lo.shape == hi.shape == (n_dims,)
        dist = td.Uniform(lo, hi)

        samples = dist.sample((batch,))
        assert samples.shape == (batch, n_dims)

        return samples

    def doth_max(self, grad_h, fx, gx, um, ul):

        bs = grad_h.shape[0]

        doth = torch.matmul(grad_h, fx)
        # doth = doth.reshape(bs, 1)
        LhG = torch.matmul(grad_h, gx)

        sign_grad_h = torch.sign(LhG).reshape(bs, 1, self.m_control)

        if self.fault == 0:
            doth = doth + torch.matmul(sign_grad_h, um.reshape(bs, self.m_control, 1)) + \
                   torch.matmul(1 - sign_grad_h, ul.reshape(bs, self.m_control, 1))
        else:
            for i in range(self.m_control):
                if i == self.fault_control_index:
                    doth = doth.reshape(bs, 1) - sign_grad_h[:, 0, i].reshape(bs, 1) * um[:, i].reshape(bs, 1) - \
                           (1 - sign_grad_h[:, 0, i].reshape(bs, 1)) * ul[:, i].reshape(bs, 1)
                else:
                    doth = doth.reshape(bs, 1) + sign_grad_h[:, 0, i].reshape(bs, 1) * um[:, i].reshape(bs, 1) + \
                           (1 - sign_grad_h[:, 0, i].reshape(bs, 1)) * ul[:, i].reshape(bs, 1)

        return doth.reshape(1, bs)
