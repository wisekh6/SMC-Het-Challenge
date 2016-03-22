import math
import numpy as np
import itertools
import json
import argparse
import StringIO
import scipy.stats
import sys
import sklearn.metrics as mt
import metric_behavior as mb
from functools import reduce
import gc

# blo
import time
import resource
import os
import gzip

INFO = True
WRITE_2B_FILES = False
WRITE_3B_FILES = False

class ValidationError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

def validate1A(data):
    data = data.split('\n')
    data = filter(None, data)
    if len(data) < 1:
        raise ValidationError("Input file contains zero lines")
    if len(data) > 1:
        raise ValidationError("Input file contains more than one line")
    data = data[0].strip()
    try:
        numeric = float(data)
    except ValueError:
        raise ValidationError("Data could not be converted to float: %s" % data)
    if math.isinf(numeric):
        raise ValidationError("Non-finite Cellularity")
    if math.isnan(numeric):
        raise ValidationError("Cellularity is NaN")
    if numeric < 0:
        raise ValidationError("Cellularity was < 0: %f" % numeric)
    if numeric > 1:
        raise ValidationError("Cellularity was > 1: %f" % numeric)

    return numeric

def calculate1A(pred, truth, err='abs'):
    if err is 'abs':
        return 1 - abs(truth - pred)
    elif err is 'sqr':
        return 1 - ((truth - pred) ** 2)
    else:
        raise KeyError('Invalid error penalty for scoring SC 1A. Choose one of "abs" or "sqr".')

def validate1B(data):
    data = data.split('\n')
    data = filter(None, data)
    if len(data) != 1:
        if len(data) == 0:
            raise ValidationError("Input file contains zero lines")
        else:
            raise ValidationError("Input file contains more than one line")
    data = data[0].strip()
    try:
        numeric = int(data)
    except ValueError:
        raise ValidationError("Data could not be converted to int: %s" % data)
    if numeric < 1:
        raise ValidationError("Number of lineages was less than 1: %d" % numeric)
    if numeric > 20:
        raise ValidationError("Number of lineages was greater than 20: %d" % numeric)
    return numeric

def calculate1B(pred, truth, method='normalized'):
    if method is 'normalized':
        return (truth + 1 - min(truth+1, abs(pred-truth))) / float(truth+1)
    elif method is 'orig':
        return abs(truth - pred) / float(truth)
    else:
        raise KeyError('Invalid method for scoring SC 1B. Choose one of "orig" or "normalized".')

def validate1C(data, nssms):
    data = data.split('\n')
    data = filter(None, data)
    data = [x.strip() for x in data]
    if len(data) < 1:
        raise ValidationError("Number of lines is less than 1")
    elif len(data) > 10:
        raise ValidationError("Number of lines is greater than 10")

    data2 = [x.split('\t') for x in data]
    for i in range(len(data)):
        if len(data2[i]) != 3:
            raise ValidationError("Number of tab separated columns in line %d is not 3" % (i+1))
        try:
            id = int(data2[i][0])
            if id != i+1:
                raise ValidationError("Cluster ID in line %d is not %d" % (i+1, i+1))
        except ValueError:
            raise ValidationError("Cluster ID in line %d can not be cast as an integer: %s" % (i+1, data2[i][0]))
        try:
            nm = int(data2[i][1])
            if nm < 1:
                raise ValidationError("Number of mutations in line %d is less than 1." % (i+1))
        except ValueError:
            raise ValidationError("Number of mutations in line %d can not be cast as an integer: %s" % (i+1, data2[i][1]))
        try:
            cf = float(data2[i][2])
            if math.isinf(cf):
                raise ValidationError("Cellular Frequency for cluster %d is non-finite" % (i+1))
            if math.isnan(cf):
                raise ValidationError("Cellular Frequency for cluster %d is NaN" % (i+1))
            if cf < 0:
                raise ValidationError("Cellular Frequency for cluster %d is negative: %f" % (i+1, cf))
            if cf > 1:
                raise ValidationError("Cellular Frequency for cluster %d is > 1: %f" % (i+1, cf))

        except ValueError:
            raise ValidationError("Cellular Frequency for cluster %d can not be cast as a float: %s" % (i+1, data2[i][2]))
    reported_nssms = sum([int(x[1]) for x in data2])
    if reported_nssms != nssms:
        raise ValidationError("Total number of reported mutations is %d. Should be %d" % (reported_nssms, nssms))
    return zip([int(x[1]) for x in data2], [float(x[2]) for x in data2])

def calculate1C(pred, truth, err='abs'):
    pred.sort(key = lambda x: x[1])
    truth.sort(key = lambda x: x[1])
    #itertools.chain(*x) flattens a list of lists
    predvs = np.array(list(itertools.chain(*[[x[1]]*x[0] for x in pred])))
    truthvs = np.array(list(itertools.chain(*[[x[1]]*x[0] for x in truth])))

    # calculate the score using the given error penalty
    if err is 'abs':
        se = abs(truthvs - predvs)
    elif err is 'sqr':
        se = ((truthvs - predvs) ** 2)
    else:
        raise KeyError('Invalid error penalty for scoring SC 1C. Choose one of "abs" or "sqr".')

    return sum(1-se)/float(len(truthvs))

def validate2A(data, nssms, return_ccm=True):
    # validate2A only fails input if..
    #   - length(truthfile) != length(mask)
    #   - if an entry in truthfile can't be cast to int
    #   - if set(truthfile) != seq(1, len(set(truthfile)), 1)
    data = data.split('\n')
    data = filter(None, data)
    if len(data) != nssms:
        printInfo("Input file contains a different number of lines than the specification file. Input: %s lines Specification: %s lines" % (len(data), nssms))
        raise ValidationError("Input file contains a different number of lines than the specification file. Input: %s lines Specification: %s lines" % (len(data), nssms))
    cluster_entries = set()
    # make a set of all entries in truth file
    for i in xrange(len(data)):
        try:
            data[i] = int(data[i])
            cluster_entries.add(data[i])
        except ValueError:
            printInfo("Cluster ID in line %d (ssm %s) can not be cast as an integer" % (i + 1, data[i][0]))
            raise ValidationError("Cluster ID in line %d (ssm %s) can not be cast as an integer" % (i + 1, data[i][0]))
    used_clusters = sorted(list(cluster_entries))
    # expect the set to be equal to seq(1, len(set), 1)
    expected_clusters = range(1, len(cluster_entries) + 1)

    if used_clusters != expected_clusters:
        printInfo("Cluster IDs used (%s) is not what is expected (%s)" % (str(used_clusters), str(expected_clusters)))
        raise ValidationError("Cluster IDs used (%s) is not what is expected (%s)" % (str(used_clusters), str(expected_clusters)))

    # make a matrix of zeros ( n x m ), n = len(truthfile), m = len(set)
    # use dtype=np.int8 for c_m/ccm because we just need 0 and 1 integer values
    c_m = np.zeros((len(data), len(cluster_entries)), dtype=np.int8)

    # for each value in truthfile, put a 1 in the m index of the n row
    for i in xrange(len(data)):
        c_m[i, data[i] - 1] = 1

    if not return_ccm:
        return c_m
    else:
        # return the dot product of c_m * t(c_m)
        # this always gives a symmetric matrix
        ccm = np.dot(c_m, c_m.T)
        return ccm

def validate2Afor3A(data, nssms):
    return validate2A(data, nssms, False)

def validate2B(filename, nssms, with_pseudo_counts=False):
    # if pseudo_counts are requested, create the matrix with the extended size
    ccm_size = nssms + np.sqrt(nssms) if with_pseudo_counts else nssms
    # we only really need the identity matrix for 2B truth matrices but we will be overwriting them anyway downstream
    ccm = np.identity(ccm_size)
    try:
        if filename.endswith('.gz'):
            gzipfile = gzip.open(str(filename), 'r')
            line_num = 0
            for line in gzipfile:
                ccm[line_num, :nssms] = np.fromstring(line, sep='\t')
                line_num += 1
            gzipfile.close()
        else:
            # TODO - optimize with line by line
            data = StringIO.StringIO(filename)
            truth_ccm = np.loadtxt(data, ndmin=2)
            ccm[:nssms, :nssms] = truth_ccm
    except ValueError as e:
        printInfo("Entry in co-clustering matrix could not be cast as a float. Error message: %s" % e.message)
        raise ValidationError("Entry in co-clustering matrix could not be cast as a float. Error message: %s" % e.message)

    actual_ccm = ccm[:nssms, :nssms]

    if actual_ccm.shape != (nssms, nssms):
        printInfo("Shape of co-clustering matrix %s is wrong.  Should be %s" % (str(actual_ccm.shape), str((nssms, nssms))))
        raise ValidationError("Shape of co-clustering matrix %s is wrong.  Should be %s" % (str(actual_ccm.shape), str((nssms, nssms))))
    if not np.allclose(actual_ccm.diagonal(), np.ones((nssms))):
        printInfo("Diagonal entries of co-clustering matrix not 1")
        raise ValidationError("Diagonal entries of co-clustering matrix not 1")
    if np.any(np.isnan(actual_ccm)):
        printInfo("Co-clustering matrix contains NaNs")
        raise ValidationError("Co-clustering matrix contains NaNs")
    if np.any(np.isinf(actual_ccm)):
        printInfo("Co-clustering matrix contains non-finite entries")
        raise ValidationError("Co-clustering matrix contains non-finite entries")
    if np.any(actual_ccm > 1):
        raise ValidationError("Co-clustering matrix contains entries greater than 1")
        printInfo("Co-clustering matrix contains entries greater than 1")
    if np.any(actual_ccm < 0):
        raise ValidationError("Co-clustering matrix contains entries less than 0")
        printInfo("Co-clustering matrix contains entries less than 0")
    if not isSymmetric(actual_ccm):
        printInfo("Co-clustering matrix is not symmetric")
        raise ValidationError("Co-clustering matrix is not symmetric")
    return ccm

def isSymmetric(x):
    '''
    Checks if a matrix is symmetric.
    Better than doing np.allclose(x.T, x) because..
        - it does it in memory without making a new x.T matrix
        - fails fast if not symmetric
    '''
    symmetricity = False
    if (x.shape[0] == x.shape[1]):
        symmetricity = True
        for i in xrange(x.shape[0]):
            symmetricity = symmetricity and np.allclose(x[i, :], x[:, i])
            if (not symmetricity):
                break
    return symmetricity

def calculate2_quaid(pred, truth):
    n = truth.shape[0]
    indices = np.triu_indices(n, k=1)
    ones = np.sum(np.abs(pred[indices] - truth[indices]) * truth[indices])
    ones_count = np.count_nonzero(truth[indices])
    if ones_count > 0:
        ones_score = 1 - ones/float(ones_count)
    else:
        ones_score = -1

    zeros = np.sum(np.abs(pred[indices] - truth[indices]) * (1 - truth[indices]))
    zeros_count = len(truth[indices]) - ones_count
    if zeros_count > 0:
        zeros_score = 1 - zeros/float(zeros_count)
    else:
        zeros_score = -1

    if ones_score == -1:
        return zeros_score
    elif zeros_score == -1:
        return ones_score
    else:
        try:
            return 2.0/(1.0/ones_score + 1.0/zeros_score)
        except Warning:
            print ones_score, zeros_score
            return 0

#@profile
def calculate2(pred, truth, full_matrix=True, method='default', pseudo_counts=None):
    '''
    Calculate the score for SubChallenge 2
    :param pred: predicted co-clustering matrix
    :param truth: true co-clustering matrix
    :param full_matrix: logical for whether to use the full matrix or just the upper triangular matrices when calculating the score
    :param method: scoring metric used, default is average of Pseudo V,
    :param pseudo_counts: logical for how many psuedo counts to add to the matrices
    :return: subchallenge 2 score for the predicted co-clustering matrix
    '''

    larger_is_worse_methods = ['pseudoV', 'sym_pseudoV'] # methods where a larger score is worse
    # y = pred.n
    y = np.array(pred.shape)[1]
    # nssms
    # recall when we did m = n + sqrt(n)
    # nssms = n, given m, using quadratic
    nssms = np.ceil(0.5 * (2*y + 1) - 0.5 * np.sqrt(4*y + 1))
    import gc

    func_dict = {
        "orig"           : calculate2_orig,
        "sqrt"           : calculate2_sqrt,
        "pseudoV"        : calculate2_pseudoV,
        "sym_pseudoV"    : calculate2_sym_pseudoV,
        "spearman"       : calculate2_spearman,
        "pearson"        : calculate2_pearson,
        "aupr"           : calculate2_aupr,
        "mcc"            : calculate2_mcc
    }
    func = func_dict.get(method, None)
    if func is None:
        scores = []
        worst_scores = []

        functions = ['pseudoV', 'pearson', 'mcc']
        # functions = ['pseudoV']
        # functions = ['pearson']
        # functions = ['mcc']

        for m in functions:
            gc.collect()
            timmie = time.time()
            scores.append(func_dict[m](pred, truth, full_matrix=full_matrix))
            timmie2 = time.time() - timmie
            printInfo("method %s took %s seconds" % (m, round(timmie2, 2)))
            # normalize the scores to be between (worst of OneCluster and NCluster scores) and (Truth score)   
        for m in functions:
            gc.collect()
            timmie = time.time()
            worst_scores.append(get_worst_score(nssms, truth, func_dict[m], larger_is_worse=(m in larger_is_worse_methods)))
            timmie2 = time.time() - timmie
            printInfo("worst scores method %s took %s seconds" % (m, round(timmie2, 2)))
        for i, m in enumerate(functions):
            if m in larger_is_worse_methods:
                scores[i] = 1 - (scores[i] / worst_scores[i])
            else:
                scores[i] = (scores[i] - worst_scores[i]) / (1 - worst_scores[i])
        return np.mean(scores)

    else:
        score = func(pc_pred, pc_truth, full_matrix=full_matrix)
        if method in larger_is_worse_methods: # normalize the scores to be between 0 and 1 where 1 is the true matrix
            worst_score = get_worst_score(nssms, truth, func, larger_is_worse=True) # and zero is the worse score of the NCluster matrix
            score = 1 - (score / worst_score)                   # and the OneCluster matrix - similar to above
        else:
            worst_score = get_worst_score(nssms, truth, func, larger_is_worse=False)
            score = (score - worst_score) / (1 - worst_score)
        return score

def calculate2_orig(pred, truth, full_matrix=True):
    n = truth.shape[0]
    if full_matrix:
        pred_cp = np.copy(pred)
        truth_cp = np.copy(truth)
        count = (n**2 - n )

    else: # make matrix upper triangular
        inds = np.triu_indices(n, k=1)
        pred_cp = pred[inds]
        truth_cp = truth[inds]
        count = (n**2 - n )/2.0
    res = np.sum(np.abs(pred_cp - truth_cp))
    res = res / count
    return 1 - res


def calculate2_sqrt(pred, truth, full_matrix=True):
    n = truth.shape[0]
    if full_matrix:
        pred_cp = np.copy(pred)
        truth_cp = np.copy(truth)
        count = (n**2 - n)
    else: # make matrix upper triangular
        pred_cp = np.triu(pred)
        truth_cp = np.triu(truth)
        count = (n**2 - n )/2.0
    res = np.sum(np.abs(pred_cp - truth_cp))
    res = res / count
    return np.sqrt(1 - res)

def calculate2_simpleKL_norm(pred, truth, rnd=0.01):
    """Normalized version of the pseudo V measure where the return values are between 0 and 1
    with 0 being the worst score and 1 being the best

    :param pred:
    :param truth:
    :param rnd: small value to replace 0 entries in both matrices with. Used to avoid dividing by zero
    :return:
    """
    return 1 - calculate2_simpleKL(pred, truth, rnd=rnd) / 4000

# Out of date!!
def calculate2_simpleKL(pred, truth, rnd=0.01):
    pred = np.abs(pred - rnd)
    n = truth.shape[0]
    indices = np.triu_indices(n, k=1)
    res = 0
    for i in range(len(indices[0])):

        if truth[indices[0][i], indices[1][i]]:
            res += np.log(pred[indices[0][i], indices[1][i]])
        else:
            res += np.log(1-pred[indices[0][i], indices[1][i]])
    return abs(res)


def calculate2_pseudoV_norm(pred, truth, rnd=0.01, max_val=4000, full_matrix=True):
    """Normalized version of the pseudo V measure where the return values are between 0 and 1
    with 0 being the worst score and 1 being the best

    :param pred:
    :param truth:
    :param rnd: small value to replace 0 entries in both matrices with. Used to avoid dividing by zero
    :param max_val: maximum pseudoV value for this scenario - any prediction that has a pseudoV score >= max_val
        will be given a score of 0
    :return:
    """
    pv_val = calculate2_pseudoV(pred, truth, rnd=rnd, full_matrix=full_matrix)
    return max(1 -  pv_val/ max_val, 0)

def calculate2_pseudoV(pred, truth, rnd=0.01, full_matrix=True, sym=False):
    if full_matrix:
        pred_cp = pred
        truth_cp = truth
    else: # make matrix upper triangular
        pred_cp = np.triu(pred)
        truth_cp = np.triu(truth)

    # Avoid dividing by zero by rounding everything less than rnd up to rnd
    # Note: it is ok to do this after making the matrix upper triangular
    # since the bottom triangle of the matrix will not affect the score

    size = np.array(pred_cp.shape)[1]
    res = 0 # result to be returned

    # do one row at a time to reduce memory usage
    for x in xrange(size):
        # (1 - rnd) will cast the pred_cp/truth_cp matrices automatically if they are int8
        pred_row = (1 - rnd) * pred_cp[x, ] + rnd
        truth_row = (1 - rnd) * truth_cp[x, ] + rnd

        pred_row /= np.sum(pred_row)
        truth_row /= np.sum(truth_row)
        if sym:
            res += np.sum(truth_row * np.log(truth_row/pred_row)) + np.sum(pred_row * np.log(pred_row/truth_row))
        else:
            res += np.sum(truth_row * np.log(truth_row/pred_row))
    return res

def calculate2_sym_pseudoV_norm(pred, truth, rnd=0.01, max_val=8000, full_matrix=True):
    """Normalized version of the symmetric pseudo V measure where the return values are between 0 and 1
    with 0 being the worst score and 1 being the best

    :param pred:
    :param truth:
    :param rnd: small value to replace 0 entries in both matrices with. Used to avoid dividing by zero
    :param max_val: maximum pseudoV value for this scenario - any prediction that has a pseudoV score >= max_val
        will be given a score of 0
    :return:
    """
    spv_val = calculate2_sym_pseudoV(pred, truth, rnd=rnd, full_matrix=full_matrix)
    return max(1 - spv_val / max_val, 0)

def calculate2_sym_pseudoV(pred, truth, rnd=0.01, full_matrix=True):
    return calculate2_pseudoV(pred, truth, rnd=rnd, full_matrix=full_matrix, sym=True)

def calculate2_spearman(pred, truth, full_matrix=True):
    # use only the upper triangular matrix of the truth and
    # prediction matrices
    n = truth.shape[0]
    if full_matrix:
        pred_cp = pred.flatten()
        truth_cp = truth.flatten()
    else:
        inds = np.triu_indices(n, k=1)
        pred_cp = pred[inds]
        truth_cp = truth[inds]

    # implement spearman coefficient since scipy implementation
    # uses the covariance of the ranks, which could be zero
    # find the rank order of both sets of data
    predr = scipy.stats.rankdata(pred_cp)
    truthr = scipy.stats.rankdata(truth_cp)
    d = truthr - predr
    n = len(d)

    d = np.divide(d, np.sqrt(n)) # avoid overflow warnings
    row = 1 - (6 * sum(np.square(d))) / ((np.square(n) - 1))

    return row

def calculate2_pearson(pred, truth, full_matrix=True):
    n = truth.shape[0]
    if full_matrix:
        pass
    else:
        inds = np.triu_indices(n, k=1)
        pred = pred[inds]
        truth = truth[inds]

    return call_pearson(pred, truth)

def call_pearson(p, t):
    pbar = 0
    tbar = 0
    N = p.shape[0]

    pbar, tbar = mymean(p, t)
    sp, st = mystd(p, t, pbar, tbar)
    res = myscale(p, t, pbar, tbar, sp, st)

    return res/(N**2 - 1.0)

def mymean(vec1, vec2):
    # np.ndarray.mean() actually costs nothing
    m1 = np.ndarray.mean(vec1)
    m2 = np.ndarray.mean(vec2)
    return m1, m2

def myscale(vec1, vec2, m1, m2, s1, s2):
    N = vec1.shape[0]
    out = 0

    # original
    # for i in xrange(N):
    #     for j in xrange(N):
    #         out += ((vec1[i, j] - m1)/s1) * ((vec2[i, j] - m2)/s2)

    # optimized - row operations
    for i in xrange(N):
        out += np.dot(((vec1[i, ] - m1)/s1), ((vec2[i, ] - m2)/s2))

    return out

def mystd(vec1, vec2, m1, m2):
    s1 = 0
    s2 = 0
    N = vec1.shape[0]
    M = float(N**2)

    # original
    # for i in xrange(N):
    #     for j in xrange(N):
    #         s1 += ((vec1[i, j] - m1)**2) / (M - 1)
    #         s2 += ((vec2[i, j] - m2)**2) / (M - 1)
    # s1 = np.sqrt(s1)
    # s2 = np.sqrt(s2)

    # optimized - row operations
    for i in xrange(N):
        s1 += np.ndarray.sum((vec1[i, ] - m1)**2)
        s2 += np.ndarray.sum((vec2[i, ] - m2)**2)
    s1 /= (M - 1)
    s2 /= (M - 1)
    s1 = np.sqrt(s1)
    s2 = np.sqrt(s2)

    return s1, s2

def calculate2_aupr(pred, truth, full_matrix=True):
    n = truth.shape[0]
    if full_matrix:
        pred_cp = pred.flatten()
        truth_cp = truth.flatten()
    else:
        inds = np.triu_indices(n, k=1)
        pred_cp = pred[inds]
        truth_cp = truth[inds]
    precision, recall, thresholds = mt.precision_recall_curve(truth_cp, pred_cp)
    aucpr = mt.auc(recall, precision)
    return aucpr

# Matthews Correlation Coefficient
# don't just use upper triangular matrix because you get na's with the AD matrix
# note about casting: should be int/float friendly for pred/truth matrices

def calculate2_mcc(pred, truth, full_matrix=True):
    n = truth.shape[0]
    ptype = str(pred.dtype)
    ttype = str(truth.dtype)
    if full_matrix:
        pred_cp = pred
        truth_cp = truth
    else:
        inds = np.triu_indices(n, k=1)
        pred_cp = pred[inds]
        truth_cp = truth[inds]

    tp = 0.0
    tn = 0.0
    fp = 0.0
    fn = 0.0

    # original
    # for i in xrange(pred_cp.shape[0]):
    #     for j in xrange(pred_cp.shape[1]):
    #         if truth_cp[i,j] and pred_cp[i,j] >= 0.5:
    #             tp = tp +1.0
    #         elif truth_cp[i,j] and pred_cp[i,j] < 0.5:
    #             fn = fn + 1.0
    #         if (not truth_cp[i,j]) and pred_cp[i,j] >= 0.5:
    #             fp = fp +1.0
    #         elif (not truth_cp[i,j]) and pred_cp[i,j] < 0.5:
    #             tn = tn + 1.0

    # optimized with fancy boolean magic algorithm to calculate MCC
    for i in xrange(pred_cp.shape[0]):
        # only round if the matrices are floats
        pred_line = np.round(pred_cp[i, ] + 10.0**(-10)) if 'float' in ptype else pred_cp[i, ]
        truth_line = np.round(truth_cp[i, ] + 10.0**(-10)) if 'float' in ttype else truth_cp[i, ]

        ors = np.logical_or(truth_line, pred_line)
        ands = np.logical_and(truth_line, pred_line)
        evalthis = truth_line.astype(np.int8) + ors + ands

        counts = np.bincount(evalthis)
        tn += counts[0]
        fp += counts[1]
        fn += counts[2]
        tp += counts[3]

    # To avoid divide-by-zero cases
    denom_terms = [(tp+fp), (tp+fn), (tn+fp), (tn+fn)]

    for index, term in enumerate(denom_terms):
        if term == 0:
            denom_terms[index] = 1
    denom = np.sqrt(reduce(np.multiply, denom_terms, 1))

    if tp == 0 and fn == 0:
        num = (tn - fp)
    elif tn == 0 and fp == 0:
        num = (tp - fn)
    else:
        num = (tp*tn - fp*fn)

    return num / float(denom)


#### SUBCHALLENGE 3 #########################################################################################

def validate3A(data, cas, nssms):
    predK = cas.shape[1]
    cluster_assignments = np.argmax(cas, 1) + 1

    data = data.split('\n')
    data = filter(None, data)
    if len(data) != predK:
        printInfo("Input file contains a different number of lines (%d) than expected (%d)")
        raise ValidationError("Input file contains a different number of lines (%d) than expected (%d)")
    data = [x.split('\t') for x in data]
    for i in range(len(data)):
        if len(data[i]) != 2:
            printInfo("Number of tab separated columns in line %d is not 2" % (i+1))
            raise ValidationError("Number of tab separated columns in line %d is not 2" % (i+1))
        try:
            data[i][0] = int(data[i][0])
            data[i][1] = int(data[i][1])
        except ValueError:
            printInfo("Entry in line %d could not be cast as integer" % (i+1))
            raise ValidationError("Entry in line %d could not be cast as integer" % (i+1))

    if [x[0] for x in data] != range(1, predK+1):
        printInfo("First column must have %d entries in acending order starting with 1" % predK)
        raise ValidationError("First column must have %d entries in acending order starting with 1" % predK)

    for i in range(len(data)):
        if data[i][1] not in set(range(predK+1)):
            printInfo("Parent node label in line %d is not valid." % (i+1))
            raise ValidationError("Parent node label in line %d is not valid." % (i+1))

    # Form descendant of dict.  Each entry, keyed by cluster number, consists of a list of nodes that are decendents of the key.
    descendant_of = dict()
    for i in range(predK+1):
        descendant_of[i] = []
    for child, parent in data:
        descendant_of[parent] += [child] + descendant_of[child]
        # gps (grandparents) are the list of nodes that are ancestors of the immediate parent
        gps = [x for x in descendant_of.keys() if parent in descendant_of[x]]
        for gp in gps:
            descendant_of[gp] += [child] + descendant_of[child]

    # Check that root has all nodes as decendants (equivalent to checking if the tree is connected)
    if set(descendant_of[0]) != set(range(1, predK+1)):
        raise ValidationError("Root of phylogeny not ancestor of all clusters / Tree is not connected. " +
                              "Phelogeny matrix: %s, Descendant_of Dictionary %s" %
                              (data, descendant_of))

    # Form AD matrix
    n = len(cluster_assignments)
    # can use int8 because only 0 and 1 integers
    ad = np.zeros((n, n), dtype=np.int8)
    for i in range(n):
        for j in range(n):
            if cluster_assignments[j] in descendant_of[cluster_assignments[i]]:
                ad[i, j] = 1

    return ad

def validate3B(filename, ccm, nssms):
    size = ccm.shape[0]
    try:
        if filename.endswith('.gz'):
            ad = np.zeros((size, size))
            gzipfile = gzip.open(str(filename), 'r')
            line_num = 0
            for line in gzipfile:
                ad[line_num, :size] = np.fromstring(line, sep='\t')
                line_num += 1
            gzipfile.close()
        else:
            ad = filename
    except ValueError:
        printInfo("Entry in AD matrix could not be cast as a float")
        raise ValidationError("Entry in AD matrix could not be cast as a float")

    if ad.shape != ccm.shape:
        printInfo("Shape of AD matrix %s is wrong.  Should be %s" % (str(ad.shape), str(ccm.shape)))
        raise ValidationError("Shape of AD matrix %s is wrong.  Should be %s" % (str(ad.shape), str(ccm.shape)))
    if not np.allclose(ad.diagonal(), np.zeros(ad.shape[0])):
        printInfo("Diagonal entries of AD matrix not 0")
        raise ValidationError("Diagonal entries of AD matrix not 0")
    if np.any(np.isnan(ad)):
        printInfo("AD matrix contains NaNs")
        raise ValidationError("AD matrix contains NaNs")
    if np.any(np.isinf(ad)):
        printInfo("AD matrix contains non-finite entries")
        raise ValidationError("AD matrix contains non-finite entries")
    if np.any(ad > 1):
        printInfo("AD matrix contains entries greater than 1")
        raise ValidationError("AD matrix contains entries greater than 1")
    if np.any(ad < 0):
        printInfo("AD matrix contains entries less than 0")
        raise ValidationError("AD matrix contains entries less than 0")
    if checkForBadTriuIndices(ad, ad.T, ccm):
        printInfo("For some i, j the sum of AD(i, j) + AD(j, i) + CCM(i, j) > 1.")
        raise ValidationError("For some i, j the sum of AD(i, j) + AD(j, i) + CCM(i, j) > 1.")

    return ad

def checkForBadTriuIndices(*matrices):
    offset = 1
    # perform np.any(ad[indices] + ad.T[indices] + ccm[indices] > 1) in memory, otherwise you're loading all the objects into memory
    # plus, doing matrix[np.triu_indices()] creates a copy which is doubly bad
    shape = matrices[0].shape
    equalShapes = True
    fail = True
    for x in matrices:
        equalShapes &= shape == x.shape
        if (not equalShapes):
            break
    if (equalShapes):
        for i in xrange(shape[0]):
            for j in xrange(i + offset, shape[0]):
                fail &= reduce(lambda x, y: x + y, [z[i, j] for z in matrices]) <= 1
                if (not fail):
                    break
    else:
        raise ValidationError('Unequal shapes passed to checkForBadTriuIndices')
    return not fail

#def calculate3A(pred_ca, pred_ad, truth_ca, truth_ad):
#    pred_ccm = np.dot(pred_ca, pred_ca.T)
#    truth_ccm = np.dot(np.dot(truth_ca, truth_ca.T))
#    return calculate3(np.dot(pred_ca, pred_ca.T), pred_ad, , truth_ad)

def calculate3Final(pred_ccm, pred_ad, truth_ccm, truth_ad):
    f = calculate2_sym_pseudoV

    scores = []
    scores.append(f(pred_ad, truth_ad))
    scores.append(f(pred_ad.T, truth_ad.T))
    truth_c = makeCMatrix(truth_ccm, truth_ad, truth_ad.T)
    pred_c = makeCMatrix(pred_ccm, pred_ad, pred_ad.T)
    scores.append(f(pred_c, truth_c))
    del pred_c, truth_c
    gc.collect()

    one_scores = []
    one_ad = mb.get_ad('OneCluster', nssms=truth_ad.shape[0])
    one_scores.append(f(one_ad, truth_ad))
    one_scores.append(f(one_ad.T, truth_ad.T))
    truth_c = makeCMatrix(truth_ccm, truth_ad, truth_ad.T)
    one_ccm = mb.get_ccm('OneCluster', nssms=truth_ccm.shape[0])
    one_c = makeCMatrix(one_ccm, one_ad, one_ad.T)
    one_scores.append(f(one_c, truth_c))
    del one_c, truth_c, one_ad, one_ccm
    gc.collect()

    n_scores = []
    n_ad = mb.get_ad('NClusterOneLineage', nssms=truth_ad.shape[0])
    n_scores.append(f(n_ad, truth_ad))
    n_scores.append(f(n_ad.T, truth_ad.T))
    truth_c = makeCMatrix(truth_ccm, truth_ad, truth_ad.T)
    n_ccm = mb.get_ccm('NClusterOneLineage', nssms=truth_ccm.shape[0])
    n_c = makeCMatrix(n_ccm, n_ad, n_ad.T)
    n_scores.append(f(n_c, truth_c))
    del n_c, truth_c, n_ad, n_ccm
    gc.collect()

    score = sum(scores) / 3.0
    one_score = sum(one_scores) / 3.0
    n_score = sum(n_scores) / 3.0

    return 1 - (score / max(one_score, n_score))

def makeCMatrix(*matrices):
    # perform (1 - *matrices) without loading all the matrices into memory
    shape = matrices[0].shape
    equalShapes = True
    for x in matrices:
        equalShapes &= shape == x.shape
        if (not equalShapes):
            break
    if (equalShapes):
        output = np.ones([shape[0], shape[0]])
        for i in xrange(shape[0]):
            output[i, ] -= reduce(lambda x, y: x + y, [z[i, ] for z in matrices])
    else:
        raise ValidationError('Unequal shapes passed to makeCMatrix')
    return output


def calculate3(pred_ccm, pred_ad, truth_ccm, truth_ad, method="sym_pseudoV", weights=None, verbose=False, pseudo_counts=True, full_matrix=True, in_mat=2):
    """
    Calculate the score for subchallenge 3 using the given metric or a weighted average of the
    given metrics, if more than one are specified.

    :param pred_ccm: predicted co-clustering matrix
    :param pred_ad: predicted ancestor-descendant matrix
    :param truth_ccm: true co-clustering matrix
    :param truth_ad: trus ancestor-descendant matrix
    :param method: method to use when evaluating the submission or list of methods to use
    :param weights: weights to use in averaging the scores of different methods.
    :param verbose: boolean for whether to display information about the score calculations
    Only used if 'method' is a list - in this case must be a list of numbers of the same length as 'method'.
    :param full_matrix: boolean for whether to use the full CCM/AD matrix when calculating the score
    :param in_mat: number representing which matrices to use in calculating the SC3 scoring metric
        Options:
            1 - use all input matrics i.e. CCM, ADM, ADM^T and CM
            2 - use all except co-clustering matrix (CCM)
            3 - use all except ancestor descendant matrix (ADM)
            4 - use all except ADM^T
            5 - use all except cousin matrix (CM)
    :return: score for the given submission to subchallenge 3 using the given metric
    """
    larger_is_worse_methods = ['sym_pseudoV_nc', 'sym_pseudoV', 'pseudoV_nc', 'pseudoV', "simpleKL_nc", 'simpleKL'] # methods where a larger score is worse

    
    pc_pred_ccm, pc_pred_ad, pc_truth_ccm, pc_truth_ad = pred_ccm, pred_ad, truth_ccm, truth_ad
    y = np.array(pc_pred_ad.shape)[1]
    nssms = np.ceil(0.5 * (2*y + 1) - 0.5 * np.sqrt(4*y + 1))

    if isinstance(method, list):
        res = [calculate3_onemetric(pc_pred_ccm, pc_pred_ad, pc_truth_ccm, pc_truth_ad,
                                    method=m, verbose=verbose, in_mat=in_mat) for m in method] # calculate the score for each method

        # normalize the scores to be between (worst of NCluster score and OneCluster score) and (Truth score)
        ncluster_score = [calculate3_onemetric(ncluster_ccm, ncluster_ad, pc_truth_ccm, pc_truth_ad,
                                               method=m, verbose=verbose, full_matrix=full_matrix, in_mat=in_mat) for m in method]
        onecluster_score = [calculate3_onemetric(onecluster_ccm, onecluster_ad, pc_truth_ccm, pc_truth_ad,
                                                 method=m, verbose=verbose, full_matrix=full_matrix, in_mat=in_mat) for m in method]
        for i in range(len(method)):
            if method[i] in larger_is_worse_methods: # normalization for methods where a larger score is worse
                worst_score = max(ncluster_score[i], onecluster_score[i]) # worst of NCluster and OneCluster scores
                res[i] = 1 - (res[i] / worst_score) # normalize the score
            else: # normalization for methods where a smaller score is worse
                worst_score = min(ncluster_score[i], onecluster_score[i])
                res[i] = (res[i] - worst_score) / (1 - worst_score)


        if weights is None: # if weights are not specified or if they cannot be normalized then default to equal weights
            weights = [1] * len(method)
        elif sum(weights) == 0:
            Warning('Weights sum to zero so they are invalid, defaulting to equal weights')
            weights = [1] * len(method)

        weights = np.array(weights) / float(sum(weights)) # normalize the weights
        score = sum(np.multiply(res, weights))
    else:
        
        score =  calculate3_onemetric(pc_pred_ccm, pc_pred_ad, pc_truth_ccm, pc_truth_ad,
                                      method=method, verbose=verbose, full_matrix=full_matrix, in_mat=in_mat)
        del pc_pred_ccm
        del pc_pred_ad
        # normalize the score to be between (worst of NCluster score and OneCluster score) and (Truth score) - similar to above
        ncluster_ccm, ncluster_ad = add_pseudo_counts(mb.get_ccm('NClusterOneLineage', nssms=nssms), mb.get_ad('NClusterOneLineage', nssms=nssms))
        ncluster_score = calculate3_onemetric(ncluster_ccm, ncluster_ad, pc_truth_ccm, pc_truth_ad,
                                              method=method, verbose=verbose, full_matrix=full_matrix, in_mat=in_mat)
        del ncluster_ccm, ncluster_ad
        onecluster_ccm, onecluster_ad = add_pseudo_counts(mb.get_ccm('OneCluster', nssms=nssms), mb.get_ad('OneCluster', nssms=nssms))
        
        onecluster_score = calculate3_onemetric(onecluster_ccm, onecluster_ad, pc_truth_ccm, pc_truth_ad,
                                                method=method, verbose=verbose, full_matrix=full_matrix, in_mat=in_mat)
        del onecluster_ccm, onecluster_ad

        print score, ncluster_score, onecluster_score
        if method in larger_is_worse_methods:
            worst_score = max(ncluster_score, onecluster_score)
            score = 1 - (score / worst_score)
        else:
            worst_score = min(ncluster_score, onecluster_score)
            score = (score - worst_score) / (1 - worst_score)
    return score

# dictionary of method names and their corresponding metric functions
method_funcs = {"pseudoV": calculate2_pseudoV,
               "simpleKL": calculate2_simpleKL,
               "sqrt": calculate2_sqrt,
               "sym_pseudoV": calculate2_sym_pseudoV,
               "pearson": calculate2_pearson,
                "spearman": calculate2_spearman,
               "aupr":calculate2_aupr,
                "mcc": calculate2_pearson,
                "orig": calculate2_orig
    }

def calculate3_onemetric(pred_ccm, pred_ad, truth_ccm, truth_ad, rnd=0.01, method="orig_nc", verbose=False, full_matrix=True, in_mat=2):
    """Calculate the score for subchallenge 3 using the given metric

    :param pred_ccm: predicted co-clustering matrix
    :param pred_ad: predicted ancestor-descendant matrix
    :param truth_ccm: true co-clustering matrix
    :param truth_ad: trus ancestor-descendant matrix
    :param method: method to use when evaluating the submission
    :param verbose: boolean for whether to display information about the score calculations
    :param full_matrix: boolean for whether to use the full CCM/AD matrix when calculating the score
    :param in_mat: number representing which matrices to use in calculating the SC3 scoring metric
        Options:
            1 - use all input matrics i.e. CCM, ADM, ADM^T and CM
            2 - use all except co-clustering matrix (CCM)
            3 - use all except ancestor descendant matrix (ADM)
            4 - use all except ADM^T
            5 - use all except cousin matrix (CM)
    :return: score for the given submission to subchallenge 3 using the given metric
    """
    # Get the cousin matrices
    truth_cous = 1 - truth_ccm - truth_ad - truth_ad.T
    pred_cous = 1 - pred_ccm - pred_ad - pred_ad.T
    if verbose:
        if(np.amax(truth_cous) > 1 or np.amin(truth_cous) < 0):
            Warning("Cousin Truth is wrong. Maximum matrix entry is greater than 1 or minimum matrix entry is less than 0")
        if(np.amax(pred_cous) > 1 or np.amin(pred_cous) < 0):
            Warning("Cousin Predicted is wrong. Maximum matrix entry is greater than 1 or minimum matrix entry is less than 0")

    # Calculate the metric measure for each specified matrix
    func = method_funcs[method]
    results = []
    ccm_res, ad_res, ad_res_t, cous_res = [float('nan')] * 4
    if method in ("pseudoV",
               "simpleKL",
               "sym_pseudoV"):
        if in_mat != 2:
            ccm_res = func(pred_ccm, truth_ccm, rnd, full_matrix=full_matrix)
            results.append(ccm_res)
        if in_mat != 3:
            ad_res = func(pred_ad, truth_ad, rnd, full_matrix=full_matrix)
            results.append(ad_res)
        if in_mat != 4:
            ad_res_t = func(np.transpose(pred_ad), np.transpose(truth_ad), rnd, full_matrix=full_matrix)
            results.append(ad_res_t)
        if in_mat != 5:
            cous_res = func(pred_cous, truth_cous, rnd, full_matrix=full_matrix)
            results.append(cous_res)
    else:
        if in_mat != 2:
            ccm_res = func(pred_ccm, truth_ccm, full_matrix=full_matrix)
            results.append(ccm_res)
        if in_mat != 3:
            ad_res = func(pred_ad, truth_ad, full_matrix=full_matrix)
            results.append(ad_res)
        if in_mat != 4 or method in ('mcc',
                                     'pearson',
                                     'spearman'):
            ad_res_t = func(np.transpose(pred_ad), np.transpose(truth_ad), full_matrix=full_matrix)
            results.append(ad_res_t)
        if in_mat != 5:
            cous_res = func(pred_cous, truth_cous, full_matrix=full_matrix)
            results.append(cous_res)

    res =  0
    n = 0
    for r in results: # TODO: fix the NA's
        if not math.isnan(r):
            n += 1
            res += r
    if n > 0:
        res = res / float(n)

    if verbose:
        print("%s for Matrices\nCC: %s, AD: %s, AD Transpose: %s, Cousin: %s\nResult: %s" %
              (method, str(ccm_res), str(ad_res), str(ad_res_t), str(cous_res), str(res)))
    return res

def parseVCF1C(data):
    data = data.split('\n')
    data = [x for x in data if x != '']
    data = [x for x in data if x[0] != '#']
    if len(data) == 0:
        raise ValidationError("Input VCF contains no SSMs")
    return [[len(data)], [len(data)]]

def parseVCF2and3(data):
    # array of lines
    data = data.split('\n')
    # array of non-blank lines
    data = [x for x in data if x != '']
    # array of non-comment lines
    data = [x for x in data if x[0] != '#']
    if len(data) == 0:
        raise ValidationError("Input VCF contains no SSMs")
    total_ssms = len(data)
    # check if line is true or false, array of 0/1's
    mask = [x[-4:] == "True" for x in data]
    # enumerate returns tuple of (index, object)
    # get array of indices that are true in mask
    mask = [i for i, x in enumerate(mask) if x]
    tp_ssms = len(mask)

    # return
    # [
    #     [ total real lines in vcf ],
    #     [ total true lines in vcf (mask) ],
    #     [ array of indices of objects in mask that are true ]
    # ]
    return [[total_ssms], [tp_ssms], mask]

def filterFPs(x, mask):
    # filters in memory, and returns a view
    # matrix[np.ix_(mask, mask)] is considered advanced indexing and creates a copy, allocating new memory
    if x.shape[0] == x.shape[1]:
        for i, m1 in enumerate(mask):
            for j, m2 in enumerate(mask):
                x[i, j] = x[m1, m2]
        # zero out "top right quadrant" of matrix after masking
        for i in xrange(len(mask)):
            for j in xrange(len(mask), x.shape[0]):
                x[i, j] = 0
        # zero out "bottom quadrants" of matrix after masking
        for i in xrange(len(mask), x.shape[0]):
            for j in xrange(x.shape[0]):
                x[i, j] = 0
        # return a view, does not allocate new memory
        return x[:len(mask), :len(mask)]
    else:
        return x[mask, :]

def add_pseudo_counts(ccm, ad=None, num=None):
    """
    Add a small number of fake mutations or 'pseudo counts' to the co-clustering and ancestor-descendant matrices for
    subchallenges 2 and 3, each in their own, new cluster. This ensures that there are not cases where
    either of these matrices has a variance of zero. These fake mutations must be added to both the predicted
    and truth matrices.

    :param ccm: co-clustering matrix
    :param ad: ancestor-descendant matrix (optional, to be compatible with subchallenge 2)
    :param num: number of pseudo counts to add
    :return: modified ccm and ad matrices
    """
    # create an m x m identity matrix where m = (ccm.n + sqrt(ccm.n))
    # copy ccm into the identity matrix
    # basically we're extending ccm with identity values

    size = np.array(ccm.shape)[1]

    if num is None:
        num = np.sqrt(size)
    elif num == 0:
        return ccm, ad

    # added dtype=ccm.dtype because some matrices (that only have integer values of 0 and 1) can use int8 instead of the default float64
    # this shoudn't cause issues downstream in calculations because there is (from what I can tell) always a float expression to cast the ints to float
    new_ccm = np.identity(size + num, dtype=ccm.dtype)
    new_ccm[:size, :size] = np.copy(ccm)
    ccm = new_ccm

    if ad is not None:
        new_ad = np.zeros([size + num]*2)
        new_ad[:size, :size] = np.copy(ad)
        new_ad[(size+num/2):(size+3*num/4), :(size)] = 1 # one quarter of the pseudo counts are ancestors of (almost) every other cluster
        new_ad[:(size), (size+3*num/4):(size+num)] = 1 # one quarter of the pseudo counts are descendants of (almost) every other cluster
        ad = new_ad                                         # half of the pseudo counts are cousins to all other clusters
        return ccm, ad

    return ccm

def add_pseudo_counts_in_place(ccm, nssms):
    # REQUIRES ccm to be at (nssms + sqrt(nssms)) size
    # aka, requires ccm to be large enough to fit the counts
    # adds pseudo counts in memory and returns a view

    final_size = nssms + np.sqrt(nssms)
    final_size = int(final_size)

    # identity-fy the portion ccm[nssms:final_size, nssms:final_size]
    for i in xrange(nssms, final_size):
        ccm[i, i] = 1

    # return :final_size bounded ccm just in case the ccm reference holds a larger view of the matrix
    return ccm[:final_size, :final_size]

#
def get_worst_score(nssms, truth_ccm, scoring_func, truth_ad=None, subchallenge="SC2", larger_is_worse=True):
    """
    Calculate the worst score for SC2 or SC3, to be used as 0 when normalizing the scores

    :param nssms: number of SSMs in the input
    :param truth_ccm: true co-clustering matrix
    :param truth_ad: true ancestor-descendent matrix (optional)
    :param subchallenge: subchallenge to use in scoring, one of 'SC2' or 'SC3'.
                If SC3 is selected then truth_ad cannot be None
    :return: worst score of NCluster and OneCluster for SC2 or SC3 (depending on the input)
    """

    if subchallenge is 'SC3':
        if truth_ad is None:
            raise ValueError('truth_ad must not be None when scoring SC3')
        else:
            if larger_is_worse:
                return max(get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'OneCluster', subchallenge),
                           get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'NCluster', subchallenge))
            else:
                return min(get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'OneCluster', subchallenge),
                           get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'NCluster', subchallenge))

    elif subchallenge is 'SC2':
        if larger_is_worse:
            return max(get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'OneCluster', subchallenge),
                       get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'NCluster', subchallenge))
        else:
            return min(get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'OneCluster', subchallenge),
                       get_bad_score(nssms, truth_ccm, scoring_func, truth_ad, 'NCluster', subchallenge))

    else:
        raise ValueError('Subchallenge must be one of SC2 or SC3')



def get_bad_score(nssms, true_ccm, score_fun, true_ad=None, scenario='OneCluster', subchallenge='SC2', pseudo_counts=None):
    if subchallenge is 'SC2':
        bad_ccm = add_pseudo_counts(get_bad_ccm(nssms, scenario), num=pseudo_counts)
        return score_fun(bad_ccm, true_ccm)
    elif subchallenge is 'SC3':
        bad_ccm, bad_ad = add_pseudo_counts(get_bad_ccm(nssms, scenario), get_bad_ad(nssms, scenario), num=pseudo_counts)
        return score_fun(bad_ccm, bad_ad, true_ccm, true_ad)
    else:
        raise ValueError('Scenario must be one of SC2 or SC3')

def get_bad_ccm(nssms, scenario='OneCluster'):
    # no need to use default float64 matrices, we're just making ones and identity matrices
    if scenario is 'OneCluster':
        return np.ones([nssms, nssms], dtype=np.int8)
    elif scenario is 'NCluster':
        return np.identity(nssms, dtype=np.int8)
    else:
        raise ValueError('Scenario must be one of OneCluster or NCluster')

def get_bad_ad(nssms, scenario='OneCluster'):
    if scenario is 'OneCluster':
        return np.zeros([nssms, nssms])
    elif scenario is 'NCluster':
        return np.triu(np.ones([nssms, nssms]), k=1)
    else:
        raise ValueError('Scenario must be one of OneCluster or NCluster')


def verify(filename, role, func, *args):
    global err_msgs
    try:
        if filename.endswith('.gz'): #pass compressed files directly to 2B or 3B validate functions
            pred = func(filename, *args)
        else:
            # really shouldn't do read() here, stores the whole thing in memory when we could read it in chunks/lines
            f = open(filename)
            pred_data = f.read()
            f.close()
            pred = func(pred_data, *args)
    except (IOError, TypeError) as e:
        err_msgs.append("Error opening %s, from function %s using file %s in : %s" %  (role, func, filename, e.strerror))
        return None
    except (ValidationError, ValueError) as e:
        err_msgs.append("%s does not validate: %s" % (role, e.value))
        return None
    return pred


challengeMapping = {
    '1A' : {
        'val_funcs' : [validate1A],
        'score_func' : calculate1A,
        'vcf_func' : None,
        'filter_func' : None
    },
    '1B' : {
        'val_funcs' : [validate1B],
        'score_func' : calculate1B,
        'vcf_func' : None,
        'filter_func' : None
    },
    '1C' : {
        'val_funcs' : [validate1C],
        'score_func' : calculate1C,
        'vcf_func' : parseVCF1C,
        'filter_func' : None
    },
    '2A' : {
        'val_funcs' : [validate2A],
        'score_func' : calculate2,
        'vcf_func' : parseVCF2and3,
        'filter_func' : filterFPs
    },
    '2B' : {
        'val_funcs' : [validate2B],
        'score_func' : calculate2,
        'vcf_func' : parseVCF2and3,
        'filter_func' : filterFPs
    },
    '3A' : {
        'val_funcs' : [validate2Afor3A, validate3A],
        'score_func' : calculate3Final,
        'vcf_func' : parseVCF2and3,
        'filter_func' : filterFPs
    },
    '3B' : {
        'val_funcs' : [validate2B, validate3B],
        'score_func' : calculate3Final,
        'vcf_func' : parseVCF2and3,
        'filter_func' : filterFPs
    },
}

def verifyChallenge(challenge, predfiles, vcf):
    global err_msgs
    if challengeMapping[challenge]['vcf_func']:
        nssms = verify(vcf, "input VCF", parseVCF1C)
        if nssms == None:
            err_msgs.append("Could not read input VCF. Exiting")
            return "NA"
    else:
        nssms = [[], []]

    if len(predfiles) != len(challengeMapping[challenge]['val_funcs']):
        err_msgs.append("Not enough input files for Challenge %s" % challenge)
        return "Invalid"

    out = []
    for (predfile, valfunc) in zip(predfiles, challengeMapping[challenge]['val_funcs']):
        args = out + nssms[0]
        out.append(verify(predfile, "prediction file for Challenge %s" % (challenge), valfunc, *args))
        if out[-1] == None:
            return "Invalid"
    return "Valid"


def scoreChallenge(challenge, predfiles, truthfiles, vcf, approx):
    mem('START %s' % challenge)
    global err_msgs

    if challengeMapping[challenge]['vcf_func']:
# 1
        nssms = verify(vcf, "input VCF", challengeMapping[challenge]['vcf_func'])
        if nssms == None:
            err_msgs.append("Could not read input VCF. Exiting")
            return "NA"
    else:
        nssms = [[], []]

    mem('VERIFY VCF %s' % vcf)

    printInfo('total lines -> ' + str(nssms[0]))
    printInfo('total truth lines -> ' + str(nssms[1]))
    printInfo('head nssms[2] -> ' + str(nssms[2][:20]))

    if len(predfiles) != len(challengeMapping[challenge]['val_funcs']) or len(truthfiles) != len(challengeMapping[challenge]['val_funcs']):
        err_msgs.append("Not enough input files for Challenge %s" % challenge)
        return "NA"

    tout = []
    pout = []

    for predfile, truthfile, valfunc in zip(predfiles, truthfiles, challengeMapping[challenge]['val_funcs']):
        if truthfile.endswith('.gz') and challenge not in ['2B', '3B']:
            err_msgs.append('Incorrect format, must input a text file for challenge %s' % challenge)
            return "NA"

        targs = tout + nssms[1]

        if challenge in ['2A']:
            # we can afford to perform the copy in add_pseudo_counts because we're just using int8 matrices
# 2
            vout = verify(truthfile, "truth file for Challenge %s" % (challenge), valfunc, *targs)
            printInfo('TRUTH DIMENSIONS -> ', vout.shape)

            if WRITE_2B_FILES:
                np.savetxt('truth2B.txt.gz', vout)

            mem('VERIFY TRUTH %s' % truthfile)
# 3
            vout2 = add_pseudo_counts(vout)

            tout.append(vout2)
            mem('APC TRUTH %s' % truthfile)
        elif challenge in ['2B']:
            # adds pseudo counts during creation to save memory by avoiding copy-required add_pseudo_counts call
            # append True to request pseudo counts in validate2B call
            targs.append(True)
            vout_with_pseudo_counts = verify(truthfile, "truth file for Challenge %s" % (challenge), valfunc, *targs)
            tout.append(vout_with_pseudo_counts)
            mem('VERIFY/APC TRUTH %s' % truthfile)
        else:
            tout.append(verify(truthfile, "truth file for Challenge %s" % (challenge), valfunc, *targs))
            mem('VERIFY TRUTH %s' % truthfile)

        printInfo('FINAL TRUTH DIMENSIONS -> ', tout[-1].shape)

        if predfile.endswith('.gz') and challenge not in ['2B', '3B']:
            err_msgs.append('Incorrect format, must input a text file for challenge %s' % challenge)
            return "NA"

        pargs = pout + nssms[0]
# 4
        pout.append(verify(predfile, "prediction file for Challenge %s" % (challenge), valfunc, *pargs))

        mem('VERIFY PRED %s' % predfile)
        printInfo('PRED DIMENSIONS -> ', pout[-1].shape)

        if challenge in ['2A'] and WRITE_2B_FILES:
            np.savetxt('pred2B.txt.gz', pout[-1])

        if tout[-1] == None or pout[-1] == None:
            return "NA"

    if challenge in ['3A'] and WRITE_3B_FILES:
        np.savetxt('pred3B.txt.gz', pout[-1])
        np.savetxt('truth3B.txt.gz', tout[-1])

    if challengeMapping[challenge]['filter_func']:
        print('Filtering Challenge %s' % challenge)
        # validate3B(pout[1], np.dot(pout[0], pout[0].T), nssms[0])
# 5
        if challenge in ['2B']:
            # save a ref of a view of the pred matrix to do pseudo counts
            predsave = pout[0]

        pout = [challengeMapping[challenge]['filter_func'](x, nssms[2]) for x in pout]
        printInfo('PRED DIMENSION(S) -> ', [p.shape for p in pout])

        mem('FILTER PRED(S)')

        if challenge in ['2A']:
            pout = [ add_pseudo_counts(*pout) ]
            mem('APC PRED')
            printInfo('FINAL PRED DIMENSION -> ', pout[-1].shape)
        elif challenge in ['2B']:
            pout = [ add_pseudo_counts_in_place(predsave, *nssms[1]) ]
            mem('APC PRED')
            printInfo('FINAL PRED DIMENSION -> ', pout[-1].shape)

        if challenge in ['3A']:
            tout[0] = np.dot(tout[0], tout[0].T)
            pout[0] = np.dot(pout[0], pout[0].T)
            mem('3A DOT')

    answer = challengeMapping[challenge]['score_func'](*(pout + tout))
    printInfo('%.16f' % answer)
    return answer

    # return challengeMapping[challenge]['score_func'](*(pout + tout))

    # return 'success'

def printInfo(*string):
    if (INFO):
        print([string])
        sys.stdout.flush()

def mem(note):
    pid = os.getpid()
    with open(os.path.join('/proc', str(pid), 'status')) as f:
        lines = f.readlines()
    _vt = [l for l in lines if l.startswith("VmSize")][0]
    vt = mem_pretty(int(_vt.split()[1]))
    _vmax = [l for l in lines if l.startswith("VmPeak")][0]
    vmax = mem_pretty(int(_vmax.split()[1]))
    _vram = [l for l in lines if l.startswith("VmRSS")][0]
    vram = mem_pretty(int(_vram.split()[1]))
    _vswap = [l for l in lines if l.startswith("VmSwap")][0]
    vswap = mem_pretty(int(_vswap.split()[1]))

    vrammax = mem_pretty(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)

    printInfo('## MEM -> total: %s (max: %s) | ram: %s (max: %s) | swap: %s @ %s' % (vt, vmax, vram, vrammax, vswap, note))

def mem_pretty(mem):
    denom = 1
    unit = 'kb'
    if (mem > 999999):
        denom = 1000000.0
        unit ='gb'
    elif (mem > 999):
        denom = 1000.0
        unit ='mb'
    return str(mem / denom) + unit

if __name__ == '__main__':
    start_time = time.time()
    global err_msgs
    err_msgs = []

    parser = argparse.ArgumentParser()
    parser.add_argument("--pred-config", default=None)
    parser.add_argument("--truth-config", default=None)
    parser.add_argument("-c", "--challenge", default=None)
    parser.add_argument("--predfiles", nargs="+")
    parser.add_argument("--truthfiles", nargs="*")
    parser.add_argument("--vcf")
    parser.add_argument("-o", "--outputfile")
    parser.add_argument('-v', action='store_true', default=False)
    parser.add_argument('--approx', action='store_true', default=False)

    args = parser.parse_args()

    if args.pred_config is not None and args.truth_config is not None:
        with open(args.pred_config) as handle:
            pred_config = {}
            for line in handle:
                try:
                    v = json.loads(line)
                    if isinstance(v, dict):
                        pred_config = dict(pred_config, **v)
                except ValueError as e:
                    pass
        with open(args.truth_config) as handle:
            truth_config = {}
            for line in handle:
                try:
                    v = json.loads(line)
                    if isinstance(v, dict):
                        truth_config = dict(truth_config, **v)
                except ValueError as e:
                    pass
        out = {}
        print "pred", pred_config
        print "truth", truth_config
        for challenge in pred_config:
            if challenge in truth_config:
                predfile = pred_config[challenge]
                vcf = truth_config[challenge]['vcf']
                truthfiles = truth_config[challenge]['truth']
                if args.v:
                    res = verifyChallenge(challenge, predfile, vcf)
                else:
                    res = scoreChallenge(challenge, predfile, truthfiles, vcf)
                out[challenge] = res
        with open(args.outputfile, "w") as handle:
            jtxt = json.dumps(out)
            handle.write(jtxt)
    else:
        if args.v:
            res = verifyChallenge(args.challenge, args.predfiles, args.vcf)
        else:
            res = scoreChallenge(args.challenge, args.predfiles, args.truthfiles, args.vcf, args.approx)

        with open(args.outputfile, "w") as handle:
            jtxt = json.dumps( { args.challenge : res } )
            handle.write(jtxt)

    mem('DONE')

    end_time = time.time() - start_time
    printInfo("run took %s seconds!" % round(end_time, 2))

    if len(err_msgs) > 0:
        for msg in err_msgs:
            print msg
        raise ValidationError("Errors encountered. If running in Galaxy see stdout for more info. The results of any successful evaluations are in the Job data.")