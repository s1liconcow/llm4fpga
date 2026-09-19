"""Floating-point specification of an 8-correspondence 2D ICP reduction.

For each scan point p and its associated map point q, minimize
  ||R(theta) p + translation - q||^2.

The host supplies c=cos(theta), s=sin(theta), translation and correspondences.
This kernel computes the rotation, residuals, Jacobian, Hessian, gradient and cost.
The host solves H delta = -g and recomputes correspondences at the updated pose.

H = [[count, 0, h02], [0, count, h12], [h02, h12, h22]]
g = [g0, g1, g2]. count=0 must return all zero outputs.
No state persists across packets. Every valid packet produces all eight outputs.
"""


def normal_equations(points, targets, tx, ty, c, s):
    h02 = h12 = h22 = g0 = g1 = g2 = cost = 0.0
    for (px, py), (qx, qy) in zip(points, targets):
        ux = c * px - s * py
        uy = s * px + c * py
        rx = ux + tx - qx
        ry = uy + ty - qy
        jx = -uy
        jy = ux
        h02 += jx
        h12 += jy
        h22 += jx * jx + jy * jy
        g0 += rx
        g1 += ry
        g2 += jx * rx + jy * ry
        cost += rx * rx + ry * ry
    return h02, h12, h22, g0, g1, g2, cost, float(len(points))
