import time

import numpy as np
import remap
import cv2
import pylab
import h5py

import template_matching
import fastremap
import ransac

from scipy.ndimage.filters import gaussian_filter
import scipy.ndimage as nd

class Warp(object):
    def __init__(self):
        pass

class RigidWarp(Warp):
    def __init__(self, R, T):
        '''R, T should take normalized coordinates to normalized coordinates, in [[j, i]].T layout.'''
        Warp.__init__(self)
        self.R = R
        self.T = T

    def warp(self, sources, dest_shape, dests=None, repeat=False):
        src_i, src_j = self.resize(dest_shape)
        src_i *= (sources[0].shape[0] - 1)
        src_j *= (sources[0].shape[1] - 1)
        if dests is None:
            dests = [np.zeros(dest_shape) for s in sources]
        for s, d in zip(sources, dests):
            remap.remap(s, src_j, src_i, d, repeat=repeat)
        return dests

    def resize(self, sz):
        dst_i, dst_j = np.mgrid[:sz[0], :sz[1]]
        dst_i = dst_i.astype(np.float32) / (sz[0] - 1)
        dst_j = dst_j.astype(np.float32) / (sz[1] - 1)
        coords = np.vstack((dst_i.ravel(), dst_j.ravel()))
        coords = (self.R * coords + self.T).A
        src_i = coords[0, :].reshape(sz)
        src_j = coords[1, :].reshape(sz)
        return src_i, src_j


class NonlinearWarp(Warp):
    def __init__(self, R, T, row_warp, column_warp):
        Warp.__init__(self)
        self.R = R
        self.T = T
        self.row_warp = row_warp
        self.column_warp = column_warp
        assert row_warp.shape == column_warp.shape
        assert np.all(~ np.isnan(row_warp))
        assert np.all(~ np.isnan(column_warp))
        assert np.all(~ np.isnan(R))
        assert np.all(~ np.isnan(T))

    def warp(self, sources, dest_shape, dests=None, repeat=False):
        if dests is None:
            dests = [np.zeros(dest_shape, dtype=s.dtype) for s in sources]
        for s, d in zip(sources, dests):
            fastremap.remap(s, d, self.R, self.T,
                            self.row_warp.astype(np.float32), self.column_warp.astype(np.float32),
                            repeat)
        return dests

    def resize(self, sz):
        normalized_i, normalized_j = np.mgrid[:sz[0], :sz[1]]
        normalized_i = normalized_i.astype(np.float32) / (sz[0] - 1)
        normalized_j = normalized_j.astype(np.float32) / (sz[1] - 1)
        rigid = self.R * np.row_stack((normalized_i.ravel(), normalized_j.ravel())) + self.T
        normalized_i = rigid[0, :].A.reshape(sz)
        normalized_j = rigid[1, :].A.reshape(sz)

        row_warp = self.row_warp
        column_warp = self.column_warp
        # resize the warps to be the size of the output (using remap)
        if row_warp.shape != tuple(sz):
            temp_i = np.linspace(0, row_warp.shape[0] - 1, sz[0]).reshape((-1, 1)).astype(np.float32)
            temp_j = np.linspace(0, row_warp.shape[1] - 1, sz[1]).reshape((1, -1)).astype(np.float32)
            temp_i, temp_j = np.broadcast_arrays(temp_i, temp_j)
            new_row = np.zeros(sz, dtype=np.float32)
            new_column = new_row.copy()
            remap.remap(row_warp, temp_j, temp_i, new_row)
            remap.remap(column_warp, temp_j, temp_i, new_column)
            row_warp = new_row
            column_warp = new_column
        return normalized_i + row_warp, normalized_j + column_warp

    def chain(self, warp2):
        '''compose this warp and warp2 - this warp takes x to y, warp2 y to z'''
        Rzx = warp2.R * self.R
        Tzx = warp2.R * self.T + warp2.T
        # rotate the yx residuals by Rzy
        X = np.row_stack((self.row_warp.ravel(), self.column_warp.ravel()))
        X = warp2.R * X
        R_row_warp = X[0, :].reshape(self.row_warp.shape).A
        R_column_warp = X[1, :].reshape(self.column_warp.shape).A
        # warp the zy residuals to x's space
        warped_row_warp, warped_column_warp = self.warp([warp2.row_warp, warp2.column_warp],
                                                        R_row_warp.shape, repeat=True)
        return NonlinearWarp(Rzx, Tzx,
                             R_row_warp + warped_row_warp,
                             R_column_warp + warped_column_warp)

    def save(self, filename):
        hf = h5py.File(filename, 'w')
        R = self.R
        T = self.T
        RW = self.row_warp
        CW = self.column_warp
        hf.create_dataset('R', R.shape, dtype=R.dtype)[...] = R
        hf.create_dataset('T', T.shape, dtype=T.dtype)[...] = T
        hf.create_dataset('row_warp', RW.shape, dtype=RW.dtype)[...] = RW
        hf.create_dataset('column_warp', CW.shape, dtype=CW.dtype)[...] = CW
        hf.close()

    @classmethod
    def load(cls, filename):
        hf = h5py.File(filename, 'r')
        R = np.matrix(hf['R'])
        T = np.matrix(hf['T'])
        rw = hf['row_warp'][...]
        cw = hf['column_warp'][...]
        hf.close()
        return cls(R, T, rw, cw)

    @classmethod
    def identity(cls, sz):
        return cls(np.matrix([[1,0],[0,1]]), np.matrix([[0],[0]]), np.zeros(sz), np.zeros(sz))

    @classmethod
    def lerp(cls, wa, wb, t):
        # use arcsin, as we expect the rotations to be small
        anglea = np.arcsin(wa.R[1, 0])
        angleb = np.arcsin(wb.R[1, 0])
        angle = (angleb - anglea) * t + anglea
        R = np.matrix([[np.cos(angle), - np.sin(angle)],
                       [np.sin(angle), np.cos(angle)]])
        T = (wb.T - wa.T) * t + wa.T
        row_warp = (1.0 - t) * wa.row_warp + t * wb.row_warp
        column_warp = (1.0 - t) * wa.column_warp + t * wb.column_warp
        return NonlinearWarp(R, T, row_warp, column_warp)

    def correct(self, t, final_warp, prevR, prevT):
        self.T += t * final_warp.T
        if prevT is not None:
            self.T += prevT
        delta_angle = t * np.arcsin(final_warp.R[1, 0])
        self.R = np.matrix([[np.cos(delta_angle), -np.sin(delta_angle)],
                            [np.sin(delta_angle), np.cos(delta_angle)]]) * self.R
        if prevR is not None:
            self.R = prevR * self.R

def fill(data, invalid=None):
    """
    Replace the value of invalid 'data' cells (indicated by 'invalid')
    by the value of the nearest valid data cell

    Input:
        data:    numpy array of any dimension
        invalid: a binary array of same shape as 'data'. True cells set where data
                 value should be replaced.
                 If None (default), use: invalid  = np.isnan(data)

    Output:
        Return a filled array.
    """

    if invalid is None: invalid = np.isnan(data)

    ind = nd.distance_transform_edt(invalid, return_distances=False, return_indices=True)
    return data[tuple(ind)]

def refine_warp(prev_warp, im1, im2, template_size, window_size, step_size, pool):
    # warp im2's coordinates to im1's space
    dest_shape = (np.array(im1.shape) // step_size) + 1
    normalized_i, normalized_j = np.mgrid[:dest_shape[0], :dest_shape[1]]
    normalized_i = normalized_i.astype(float) / (dest_shape[0] - 1)
    normalized_j = normalized_j.astype(float) / (dest_shape[1] - 1)
    row_warp, column_warp = prev_warp.resize(dest_shape)
    orig_row_warp = row_warp.copy()
    orig_column_warp = column_warp.copy()
    weights = np.zeros_like(row_warp, dtype=np.float32)

    # Refine using template matching

    # upper left points
    template_i = ((im1.shape[0] - 1) * normalized_i - template_size // 2).astype(np.int32)
    template_j = ((im1.shape[1] - 1) * normalized_j - template_size // 2).astype(np.int32)
    window_i = ((im2.shape[0] - 1) * row_warp - window_size // 2).astype(np.int32)
    window_j = ((im2.shape[1] - 1) * column_warp - window_size // 2).astype(np.int32)

    st = time.time()
    icoords, jcoords = np.mgrid[:dest_shape[0], :dest_shape[1]]

    new_rows = np.zeros(dest_shape, np.int32)
    new_cols = np.zeros(dest_shape, np.int32)
    def match_row(rowidx):
        template_matching.best_matches(template_i[rowidx, :], template_j[rowidx, :],
                                       window_i[rowidx, :], window_j[rowidx, :],
                                       template_size, window_size,
                                       im1, im2,
                                       new_rows[rowidx, :], new_cols[rowidx, :], weights[rowidx, :])

    newpts = pool.map_async(match_row, range(dest_shape[0]))
    newpts.wait()
    print "took", time.time() - st

    # adjust to center, convert to normalized coords
    new_rows += template_size // 2
    new_cols += template_size // 2
    row_warp = new_rows.astype(np.float32) / (im2.shape[0] - 1)
    column_warp = new_cols.astype(np.float32) / (im2.shape[1] - 1)

    # estimate rigid transformation from good matches
    medweight = np.median(weights[weights > -1])
    mask = weights > medweight
    X = np.row_stack((normalized_i[mask], normalized_j[mask]))
    Y = np.row_stack((row_warp[mask], column_warp[mask]))
    R, T = ransac.estimate_rigid_transformation(X, Y)

    newX = R * np.row_stack((normalized_i.ravel(), normalized_j.ravel()))  + T
    new_normalized_i = newX[0, :].A.reshape(dest_shape)
    new_normalized_j = newX[1, :].A.reshape(dest_shape)

    # convert to residuals
    row_warp -= new_normalized_i
    column_warp -= new_normalized_j

    # smooth and normalize residuals
    # we use weights squared, and cut off anything with a score less than 4.0
    weights[weights < 4.0] = 0.0
    weights = weights ** 2
    weighted_r = row_warp * weights
    weighted_c = column_warp * weights

    # Loop enough that there should be weight everywhere.
    # Filter radius for sigma=3 is approximately 10
    for iter in range(max(weights.shape) / 10 + 1):
        # weights = cv2.GaussianBlur(weights, (0, 0), 5)
        # weighted_r = cv2.GaussianBlur(weighted_r, (0, 0), 5)
        # weighted_c = cv2.GaussianBlur(weighted_c, (0, 0), 5)
        weights = gaussian_filter(weights, 3, mode='constant', cval=0)
        weighted_r = gaussian_filter(weighted_r, 3, mode='constant', cval=0)
        weighted_c = gaussian_filter(weighted_c, 3, mode='constant', cval=0)

    # Use the rigid transformation anywhere we don't have new data
    zeromask = weights == 0
    weights[zeromask] = 1
    weighted_r[zeromask] = 0
    weighted_c[zeromask] = 0
    return NonlinearWarp(R, T, weighted_r / weights, weighted_c / weights)

def best_match_python(pt1, pt2, im1, im2, template_size, window_size):
    # cut out template
    r1, c1 = pt1
    r1 -= template_size // 2
    c1 -= template_size // 2
    if r1 < 0: r1 = 0
    if r1 > im1.shape[0] - template_size: r1 = im1.shape[0] - template_size
    if c1 < 0: c1 = 0
    if c1 > im1.shape[1] - template_size: c1 = im1.shape[1] - template_size
    template = im1[r1:(r1 + template_size), c1:(c1 + template_size)]
    # cut out window
    r2, c2 = pt2
    r2 -= window_size // 2
    c2 -= window_size // 2
    if r2 < 0: r2 = 0
    if r2 > im2.shape[0] - window_size: r2 = im2.shape[0] - window_size
    if c2 < 0: c2 = 0
    if c2 > im2.shape[1] - window_size: c2 = im2.shape[1] - window_size
    window = im2[r2:(r2 + window_size), c2:(c2 + window_size)]
    # Run normalized cross-correlation
    match = cv2.matchTemplate(window, template, cv2.TM_CCORR_NORMED)
    # template_matching.best_match(r1, c1, r2, c2, template_size, window_size,
    # im1, im2)
    # find highest point
    bestr, bestc = np.unravel_index(match.argmax(), match.shape)
    score = (match[bestr, bestc] - match.mean()) / match.std()
    if np.isnan(score):
        score = 0
    print "python adjustment", pt1, pt2, r2 - (r1 - pt1[0]), c2 - (c1 - pt1[1])
    bestr = r2 + bestr - (r1 - pt1[0])
    bestc = c2 + bestc - (c1 - pt1[1])
    return bestr, bestc, score
