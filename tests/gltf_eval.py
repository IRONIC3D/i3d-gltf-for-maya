"""Independent glTF 2.0 reference evaluator for tests. No Maya imports.

Implements the glTF spec directly and shares no code with the importer's builders or
its core.animation / core.deformers modules (only the file reader and accessor decoding
are reused):

  * node local matrix = T * R * S (or ``matrix``), animated TRS sampled with the spec's
    interpolation rules (STEP, LINEAR with quaternion slerp per spec Appendix C,
    CUBICSPLINE Hermite with tangents scaled by the key interval, quaternions normalised);
  * world = parent world * local;
  * morph targets: p = base + sum(w_i * POSITION delta_i), weights from the animation,
    else node.weights, else mesh.weights, else 0;
  * skinning: p_world = sum(w_j * jointWorld_j * inverseBind_j * p) over every
    JOINTS_n / WEIGHTS_n set (weights normalised); the mesh node's own transform is ignored.

``Evaluator(path).positions(t)`` -> {node index: {primitive index: [(x, y, z), ...]}}
world-space positions in metres, glTF axes, one per accessor element.
"""

import math

from i3d_gltf.core.accessors import AccessorReader
from i3d_gltf.core.reader import load


# 4x4 matrices: row-major nested lists, column vectors (m[row][col]) ----------------------

def mat_identity():
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def mat_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def mat_from_gltf(flat):
    """glTF stores matrices column-major."""
    return [[float(flat[c * 4 + r]) for c in range(4)] for r in range(4)]


def mat_trs(t, q, s):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    m = mat_identity()
    for i in range(3):
        for j in range(3):
            m[i][j] = r[i][j] * s[j]
        m[i][3] = t[i]
    return m


def mat_point(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


# interpolation (spec) ---------------------------------------------------------------------

def spec_slerp(a, b, t):
    """glTF 2.0 Appendix C slerp (takes the shortest path)."""
    d = sum(x * y for x, y in zip(a, b))
    if abs(d) > 0.999999:
        out = [x + t * ((y if d >= 0 else -y) - x) for x, y in zip(a, b)]
    else:
        ang = math.acos(abs(d))
        s = 1.0 if d >= 0 else -1.0
        k0 = math.sin(ang * (1 - t)) / math.sin(ang)
        k1 = s * math.sin(ang * t) / math.sin(ang)
        out = [k0 * x + k1 * y for x, y in zip(a, b)]
    n = math.sqrt(sum(x * x for x in out))
    return [x / n for x in out]


class Channel(object):
    def __init__(self, times, flat, ncomp, interp, rotation):
        self.times = times
        self.interp = interp
        self.rotation = rotation
        n = len(times)
        el = lambda i: [flat[i * ncomp + c] for c in range(ncomp)]  # noqa: E731
        if interp == "CUBICSPLINE":
            self.a = [el(3 * k) for k in range(n)]
            self.v = [el(3 * k + 1) for k in range(n)]
            self.b = [el(3 * k + 2) for k in range(n)]
        else:
            self.v = [el(k) for k in range(n)]

    def _norm(self, q):
        if not self.rotation:
            return list(q)
        n = math.sqrt(sum(x * x for x in q))
        return [x / n for x in q]

    def sample(self, t):
        ts = self.times
        if t <= ts[0]:
            return self._norm(self.v[0])
        if t >= ts[-1]:
            return self._norm(self.v[-1])
        k = 0
        while ts[k + 1] <= t:
            k += 1
        td = ts[k + 1] - ts[k]
        u = (t - ts[k]) / td
        if self.interp == "STEP":
            return self._norm(self.v[k])
        if self.interp == "LINEAR":
            if self.rotation:
                return spec_slerp(self._norm(self.v[k]), self._norm(self.v[k + 1]), u)
            return [p + u * (q - p) for p, q in zip(self.v[k], self.v[k + 1])]
        u2, u3 = u * u, u * u * u
        out = [(2 * u3 - 3 * u2 + 1) * self.v[k][c] + td * (u3 - 2 * u2 + u) * self.b[k][c]
               + (-2 * u3 + 3 * u2) * self.v[k + 1][c] + td * (u3 - u2) * self.a[k + 1][c]
               for c in range(len(self.v[k]))]
        return self._norm(out)


# evaluator --------------------------------------------------------------------------------

class Evaluator(object):
    def __init__(self, path, animation=0):
        self.doc = load(path)
        self.reader = AccessorReader(self.doc)
        doc = self.doc
        self.parent = {}
        for i, n in enumerate(doc.nodes):
            for c in n.children:
                self.parent[c] = i
        scene = doc.scene if doc.scene is not None else 0
        if doc.scenes:
            roots = doc.scenes[scene].nodes
        else:
            roots = [i for i in range(len(doc.nodes)) if i not in self.parent]
        self.scene_nodes = []
        stack = list(roots)
        while stack:
            i = stack.pop()
            self.scene_nodes.append(i)
            stack.extend(doc.nodes[i].children)
        self.channels = {}
        if animation is not None and animation < len(doc.animations):
            anim = doc.animations[animation]
            for ch in anim.channels:
                if ch.target_node is None or ch.target_path not in ("translation", "rotation", "scale", "weights"):
                    continue
                s = anim.samplers[ch.sampler]
                times = list(self.reader.read(s.input).values)
                flat = list(self.reader.read_floats(s.output).values)
                if ch.target_path == "weights":
                    ncomp = len(flat) // len(times) // (3 if s.interpolation == "CUBICSPLINE" else 1)
                else:
                    ncomp = 4 if ch.target_path == "rotation" else 3
                self.channels[(ch.target_node, ch.target_path)] = Channel(times, flat, ncomp, s.interpolation,
                                                                          ch.target_path == "rotation")
        self._prims = {}

    def time_range(self):
        ts = [t for ch in self.channels.values() for t in (ch.times[0], ch.times[-1])]
        return (min(ts), max(ts)) if ts else (0.0, 0.0)

    def local(self, i, t):
        n = self.doc.nodes[i]
        keys = [(i, p) in self.channels for p in ("translation", "rotation", "scale")]
        if n.matrix is not None and not any(keys):
            return mat_from_gltf(n.matrix)
        tr = n.translation or [0.0, 0.0, 0.0]
        ro = n.rotation or [0.0, 0.0, 0.0, 1.0]
        sc = n.scale or [1.0, 1.0, 1.0]
        if keys[0]:
            tr = self.channels[(i, "translation")].sample(t)
        if keys[1]:
            ro = self.channels[(i, "rotation")].sample(t)
        if keys[2]:
            sc = self.channels[(i, "scale")].sample(t)
        return mat_trs(tr, ro, sc)

    def worlds(self, t):
        out = {}

        def world(i):
            if i not in out:
                p = self.parent.get(i)
                loc = self.local(i, t)
                out[i] = loc if p is None else mat_mul(world(p), loc)
            return out[i]
        for i in self.scene_nodes:
            world(i)
        return out

    def _prim_data(self, mesh_index, prim_index):
        key = (mesh_index, prim_index)
        if key not in self._prims:
            prim = self.doc.meshes[mesh_index].primitives[prim_index]
            r = self.reader

            def vec3(acc):
                v = r.read_floats(acc).values
                return [(v[3 * k], v[3 * k + 1], v[3 * k + 2]) for k in range(len(v) // 3)]
            pos = vec3(prim.attributes["POSITION"])
            targets = [vec3(tg["POSITION"]) if "POSITION" in tg else None for tg in prim.targets]
            sets = []
            k = 0
            while "JOINTS_%d" % k in prim.attributes and "WEIGHTS_%d" % k in prim.attributes:
                j = r.read(prim.attributes["JOINTS_%d" % k]).values
                w = r.read_floats(prim.attributes["WEIGHTS_%d" % k]).values
                sets.append((j, w))
                k += 1
            self._prims[key] = (pos, targets, sets)
        return self._prims[key]

    def weights(self, i, t):
        n = self.doc.nodes[i]
        mesh = self.doc.meshes[n.mesh]
        count = max((len(p.targets) for p in mesh.primitives), default=0)
        w = None
        if (i, "weights") in self.channels:
            w = self.channels[(i, "weights")].sample(t)
        elif n.weights is not None:
            w = n.weights
        elif mesh.weights is not None:
            w = mesh.weights
        w = list(w or [])
        return (w + [0.0] * count)[:count]

    def positions(self, t, nodes=None):
        worlds = self.worlds(t)
        out = {}
        for i in (nodes if nodes is not None else self.scene_nodes):
            n = self.doc.nodes[i]
            if n.mesh is None:
                continue
            w = self.weights(i, t)
            skin = self.doc.skins[n.skin] if n.skin is not None else None
            joint_mats = None
            if skin is not None:
                if skin.inverse_bind_matrices is not None:
                    ibm = self.reader.read_floats(skin.inverse_bind_matrices).values
                    ibms = [mat_from_gltf(ibm[16 * k:16 * k + 16]) for k in range(len(skin.joints))]
                else:
                    ibms = [mat_identity() for _ in skin.joints]
                joint_mats = [mat_mul(worlds[j], ibms[k]) for k, j in enumerate(skin.joints)]
            per_prim = {}
            for pi, prim in enumerate(self.doc.meshes[n.mesh].primitives):
                if "POSITION" not in prim.attributes or prim.mode not in (4, 5, 6):
                    continue
                pos, targets, sets = self._prim_data(n.mesh, pi)
                pts = []
                for v, p in enumerate(pos):
                    x, y, z = p
                    for ti, d in enumerate(targets):
                        if d is not None and ti < len(w) and w[ti]:
                            x += w[ti] * d[v][0]
                            y += w[ti] * d[v][1]
                            z += w[ti] * d[v][2]
                    if joint_mats is not None and sets:
                        acc = [0.0, 0.0, 0.0]
                        total = 0.0
                        for jv, wv in sets:
                            for c in range(4):
                                wt = wv[4 * v + c]
                                if wt:
                                    q = mat_point(joint_mats[jv[4 * v + c]], (x, y, z))
                                    acc[0] += wt * q[0]
                                    acc[1] += wt * q[1]
                                    acc[2] += wt * q[2]
                                    total += wt
                        pts.append(tuple(a / total for a in acc) if total else mat_point(worlds[i], (x, y, z)))
                    else:
                        pts.append(mat_point(worlds[i], (x, y, z)))
                per_prim[pi] = pts
            out[i] = per_prim
        return out
