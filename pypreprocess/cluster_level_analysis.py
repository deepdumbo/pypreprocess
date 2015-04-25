""" Utilities to describe the result of cluster-level analysis of statistical maps.

Author: Bertrand Thirion, 2015 
"""
import numpy as np
from scipy.ndimage import label, maximum_filter
from scipy.stats import norm

from nibabel import load
from nilearn.image.resampling import coord_transform
from nilearn._utils.niimg_conversions import check_niimg, _check_same_fov


def fdr_threshold(z_vals, alpha):
    """ return the BH fdr for the input z_vals"""
    z_vals_ = - np.sort(- z_vals)
    p_vals = norm.sf(z_vals_)
    n_samples = len(p_vals)
    pos = p_vals < alpha * np.linspace(
        .5 / n_samples, 1 - .5 / n_samples, n_samples)
    if pos.any():
        return (z_vals_[pos][-1] - 1.e-8)
    else:
        return np.infty


def fdr_pvalues(z_vals):
    """ return the fdr pvalues for the z-variate"""
    order = np.argsort(- z_vals)
    p_vals = norm.sf(z_vals[order])
    n_samples = len(z_vals)
    fdr = np.minimum(1, p_vals / np.linspace(1. / n_samples, 1., n_samples))
    for i in range(n_samples - 1, 0, -1):
        fdr[i - 1] = min(fdr[i - 1], fdr[i])

    inv_order = np.empty(n_samples, 'int')
    inv_order[order] = np.arange(n_samples)
    return fdr[inv_order]


def empirical_pvalue(z_score, ref):
    """ retrun the percentile """
    ranks = np.searchsorted(np.sort(ref), z_score)
    return 1 - ranks * 1. / ref.size


def cluster_stats(stat_img, mask_img, threshold, height_control='fpr',
                  cluster_threshold=0, nulls={}):
    """
    Return a list of clusters, each cluster being represented by a
    dictionary. Clusters are sorted by descending size order. Within
    each cluster, local maxima are sorted by descending statical value

    Parameters
    ----------
    stat_img: Niimg-like object,
       statsitical image (presumably in z scale)
    mask_img: Niimg-like object,
        mask image
    threshold: float,
        cluster forming threshold (either a p-value or z-scale value)
    height_control: string
        false positive control meaning of cluster forming
        threshold: 'fpr'|'fdr'|'bonferroni'|'none'
    cluster_threshold: int or float,
        cluster size threshold
    nulls: dictionary,
        statistics of the null distribution

    Notes
    -----
    If there is no cluster, an empty list is returned
    """
    # Masking
    mask_img, stat_img = check_niimg(mask_img), check_niimg(stat_img)
    if not _check_same_fov(mask_img, stat_img):
        raise ValueError('mask_img and stat_img do not have the same fov')
    mask = mask_img.get_data().astype(np.bool)
    affine = mask_img.get_affine()
    stat_map = stat_img.get_data() * mask
    n_voxels = mask.sum()

    # Thresholding
    if height_control == 'fpr':
        z_threshold = norm.isf(threshold)
    elif height_control == 'fdr':
        z_threshold = fdr_threshold(stat_map[mask], threshold)
    elif height_control == 'bonferroni':
        z_threshold = norm.isf(threshold / n_voxels)
    else:  # Brute-force thresholding
        z_threshold = threshold

    p_threshold = norm.sf(z_threshold)
    # General info
    info = {'n_voxels': n_voxels,
            'threshold_z': z_threshold,
            'threshold_p': p_threshold,
            'threshold_pcorr': np.minimum(1, p_threshold * n_voxels)}

    above_th = stat_map > z_threshold
    above_values = stat_map * above_th
    if (above_th == 0).all():
        return [], info

    # Extract connected components above threshold
    labels, n_labels = label(above_th)

    # Extract the local maxima anove the threshold
    maxima_mask = (above_values ==
                   np.maximum(z_threshold, maximum_filter(above_values, 3)))
    x, y, z = np.array(np.where(maxima_mask))
    maxima_coords = np.array(coord_transform(x, y, z, affine)).T
    maxima_labels = labels[maxima_mask]
    maxima_values = above_values[maxima_mask]

    # FDR-corrected p-values
    max_fdr_pvalues = fdr_pvalues(stat_map[mask])[maxima_mask[mask]]

    # Default "nulls"
    if not 'zmax' in nulls:
        nulls['zmax'] = 'bonferroni'
    if not 'smax' in nulls:
        nulls['smax'] = None
    if not 's' in nulls:
        nulls['s'] = None

    # Make list of clusters, each cluster being a dictionary
    clusters = []
    for k in range(n_labels):
        cluster_size = np.sum(labels == k + 1)
        if cluster_size >= cluster_threshold:

            # get the position of the maxima that belong to that cluster
            in_cluster = maxima_labels == k + 1

            # sort the maxima by decreasing statistical value
            max_vals = maxima_values[in_cluster]
            sorted = max_vals.argsort()[::-1]

            # Report significance levels in each cluster
            z_score = max_vals[sorted]
            p_values = norm.sf(z_score)

            # Voxel-level corrected p-values
            fwer_pvalue = None
            if nulls['zmax'] == 'bonferroni':
                fwer_pvalue = np.minimum(1, p_values * n_voxels)
            elif isinstance(nulls['zmax'], np.ndarray):
                fwer_pvalue = empirical_pvalue(
                    clusters['zscore'], nulls['zmax'])

            # Cluster-level p-values (corrected)
            cluster_fwer_pvalue = None
            if isinstance(nulls['smax'], np.ndarray):
                cluster_fwer_pvalue = empirical_pvalue(
                    cluster_size, nulls['smax'])

            # Cluster-level p-values (uncorrected)
            cluster_pvalue = None
            if isinstance(nulls['s'], np.ndarray):
                cluster_pvalue = empirical_pvalue(
                    cluster_size, nulls['s'])

            # write all this into the cluster structure
            clusters.append({
                    'size': cluster_size,
                    'maxima': maxima_coords[in_cluster][sorted],
                    'zscore': z_score,
                    'fdr_pvalue': max_fdr_pvalues[in_cluster][sorted],
                    'pvalue': p_values,
                    'fwer_pvalue': fwer_pvalue,
                    'cluster_fwer_pvalue': cluster_fwer_pvalue,
                    'cluster_pvalue': cluster_pvalue
                    })

    # Sort clusters by descending size order
    order = np.argsort(- np.array([cluster['size'] for cluster in clusters]))
    clusters = [clusters[i] for i in order]

    return clusters, info
