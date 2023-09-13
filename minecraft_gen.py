import numpy as np
from scipy.spatial import Voronoi
from skimage.draw import polygon
from noise import snoise3
from skimage import exposure
from scipy import ndimage
from scipy.interpolate import interp1d


def voronoi(points, size):
    # Add points at edges to eliminate infinite ridges
    edge_points = size * np.array([[-1, -1], [-1, 2], [2, -1], [2, 2]])
    new_points = np.vstack([points, edge_points])

    # Calculate Voronoi tessellation
    vor = Voronoi(new_points)

    return vor


def voronoi_map(vor, size):
    # Calculate Voronoi map
    vor_map = np.zeros((size, size), dtype=np.uint32)

    for i, region in enumerate(vor.regions):
        # Skip empty regions and infinte ridge regions
        if len(region) == 0 or -1 in region:
            continue
        # Get polygon vertices
        x, y = np.array([vor.vertices[i][::-1] for i in region]).T
        # Get pixels inside polygon
        rr, cc = polygon(x, y)
        # Remove pixels out of image bounds
        in_box = np.where((0 <= rr) & (rr < size) & (0 <= cc) & (cc < size))
        rr, cc = rr[in_box], cc[in_box]
        # Paint image
        vor_map[rr, cc] = i

    return vor_map


def relax(points, size, k=10):
    new_points = points.copy()
    for _ in range(k):
        vor = voronoi(new_points, size)
        new_points = []
        for i, region in enumerate(vor.regions):
            if len(region) == 0 or -1 in region:
                continue
            poly = np.array([vor.vertices[i] for i in region])
            center = poly.mean(axis=0)
            new_points.append(center)
        new_points = np.array(new_points).clip(0, size)
    return new_points


def noise_map(size, res, seed, *, octaves=1, persistence=0.5, lacunarity=2.0, map_seed):
    scale = size / res
    return np.array(
        [
            [
                snoise3(
                    (x + 0.1) / scale,
                    y / scale,
                    seed + map_seed,
                    octaves=octaves,
                    persistence=persistence,
                    lacunarity=lacunarity,
                )
                for x in range(size)
            ]
            for y in range(size)
        ]
    )


def histeq(img, alpha=1):
    img_cdf, bin_centers = exposure.cumulative_distribution(img)
    img_eq = np.interp(img, bin_centers, img_cdf)
    img_eq = np.interp(img_eq, (0, 1), (-1, 1))
    return alpha * img_eq + (1 - alpha) * img


def average_cells(vor, data):
    """Returns the average value of data inside every voronoi cell"""
    size = vor.shape[0]
    count = np.max(vor) + 1

    sum_ = np.zeros(count)
    count = np.zeros(count)

    for i in range(size):
        for j in range(size):
            p = vor[i, j]
            count[p] += 1
            sum_[p] += data[i, j]

    average = np.divide(sum_, count, out=np.zeros_like(count), where=count != 0)

    return average


def fill_cells(vor, data):
    size = vor.shape[0]
    image = np.zeros((size, size))

    for i in range(size):
        for j in range(size):
            p = vor[i, j]
            image[i, j] = data[p]

    return image


def color_cells(vor, data, dtype=int):
    size = vor.shape[0]
    image = np.zeros((size, size, 3))

    for i in range(size):
        for j in range(size):
            p = vor[i, j]
            image[i, j] = data[p]

    return image.astype(dtype)


def quantize(data, n):
    bins = np.linspace(-1, 1, n + 1)
    return (np.digitize(data, bins) - 1).clip(0, n - 1)


def gradient(im_smooth):
    gradient_x = im_smooth.astype(float)
    gradient_y = im_smooth.astype(float)

    kernel = np.arange(-1, 2).astype(float)
    kernel = -kernel / 2

    gradient_x = ndimage.convolve(gradient_x, kernel[np.newaxis])
    gradient_y = ndimage.convolve(gradient_y, kernel[np.newaxis].T)

    return gradient_x, gradient_y


def sobel(im_smooth):
    gradient_x = im_smooth.astype(float)
    gradient_y = im_smooth.astype(float)

    kernel = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]])

    gradient_x = ndimage.convolve(gradient_x, kernel)
    gradient_y = ndimage.convolve(gradient_y, kernel.T)

    return gradient_x, gradient_y


def compute_normal_map(gradient_x, gradient_y, intensity=1):
    width = gradient_x.shape[1]
    height = gradient_x.shape[0]
    max_x = np.max(gradient_x)
    max_y = np.max(gradient_y)

    max_value = max_x

    if max_y > max_x:
        max_value = max_y

    normal_map = np.zeros((height, width, 3), dtype=np.float32)

    intensity = 1 / intensity

    strength = max_value / (max_value * intensity)

    normal_map[..., 0] = gradient_x / max_value
    normal_map[..., 1] = gradient_y / max_value
    normal_map[..., 2] = 1 / strength

    norm = np.sqrt(
        np.power(normal_map[..., 0], 2)
        + np.power(normal_map[..., 1], 2)
        + np.power(normal_map[..., 2], 2)
    )

    normal_map[..., 0] /= norm
    normal_map[..., 1] /= norm
    normal_map[..., 2] /= norm

    normal_map *= 0.5
    normal_map += 0.5

    return normal_map


def get_normal_map(im, intensity=1.0):
    sobel_x, sobel_y = sobel(im)
    normal_map = compute_normal_map(sobel_x, sobel_y, intensity)
    return normal_map


def get_normal_light(height_map_):
    normal_map_ = get_normal_map(height_map_)[:, :, 0:2].mean(axis=2)
    normal_map_ = np.interp(normal_map_, (0, 1), (-1, 1))
    return normal_map_


def apply_height_map(im_map, smooth_map, height_map, land_mask):
    normal_map = get_normal_light(height_map)
    normal_map = normal_map * land_mask + smooth_map / 2 * (~land_mask)

    normal_map = np.interp(normal_map, (-1, 1), (-192, 192))

    normal_map_color = np.repeat(normal_map[:, :, np.newaxis], 3, axis=-1)
    normal_map_color = normal_map_color.astype(int)

    out_map = im_map + normal_map_color
    return out_map, normal_map


def bezier(x1, y1, x2, y2, a):
    p1 = np.array([0, 0])
    p2 = np.array([x1, y1])
    p3 = np.array([x2, y2])
    p4 = np.array([1, a])

    return lambda t: (
        (1 - t) ** 3 * p1
        + 3 * (1 - t) ** 2 * t * p2
        + 3 * (1 - t) * t**2 * p3
        + t**3 * p4
    )


def bezier_lut(x1, y1, x2, y2, a):
    t = np.linspace(0, 1, 256)
    f = bezier(x1, y1, x2, y2, a)
    curve = np.array([f(t_) for t_ in t])

    return interp1d(*curve.T)


def filter_map(h_map, smooth_h_map, x1, y1, x2, y2, a, b):
    f = bezier_lut(x1, y1, x2, y2, a)
    output_map = b * h_map + (1 - b) * smooth_h_map
    output_map = f(output_map.clip(0, 1))
    return output_map


def get_boundary(vor_map, kernel=1, *, size):
    boundary_map = np.zeros_like(vor_map, dtype=bool)
    n, m = vor_map.shape

    clip = lambda x: max(0, min(size - 1, x))

    def check_for_mult(a):
        b = a[0]
        for i in range(len(a) - 1):
            if a[i] != b:
                return 1
        return 0

    for i in range(n):
        for j in range(m):
            boundary_map[i, j] = check_for_mult(
                vor_map[
                    clip(i - kernel) : clip(i + kernel + 1),
                    clip(j - kernel) : clip(j + kernel + 1),
                ].flatten()
            )

    return boundary_map


def filter_inbox(pts, *, size):
    inidx = np.all(pts < size, axis=1)
    return pts[inidx]


def generate_trees(n, *, size):
    trees = np.random.randint(0, size - 1, (n, 2))
    trees = relax(trees, size, k=10).astype(np.uint32)
    trees = filter_inbox(trees, size=size)
    return trees


def place_trees(n, mask, a=0.5, *, river_land_mask, adjusted_height_river_map, size):
    trees = generate_trees(n, size=size)
    rr, cc = trees.T

    output_trees = np.zeros((size, size), dtype=bool)
    output_trees[rr, cc] = True
    output_trees = (
        output_trees * (mask > a) * river_land_mask * (adjusted_height_river_map < 0.5)
    )

    output_trees = np.array(np.where(output_trees == 1))[::-1].T
    return output_trees
