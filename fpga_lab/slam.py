"""Bounded 2D scan-to-map SLAM with the generated normal-equation RTL in the loop.

The estimator receives scans and odometry only. Truth/walls are evaluation data.
Correspondence search, the 3x3 solve and map storage run on the host.
"""
from __future__ import annotations

import json
import bz2
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .cosim import HardwareSession, build_server
from .problem import digest, write_json
from .slam_kernel import BATCH, FIELD_NAMES, decode_result, matrices, oracle, pack, specification


def wrap(angle):
    return (angle + np.pi) % (2*np.pi) - np.pi


def rotation(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s], [s, c]])


def transform(points, pose):
    return points @ rotation(pose[2]).T + pose[:2]


def compose(pose, delta):
    return np.r_[pose[:2] + rotation(pose[2]) @ delta[:2], wrap(pose[2]+delta[2])]


def relative(a, b):
    return np.r_[rotation(a[2]).T @ (b[:2]-a[:2]), wrap(b[2]-a[2])]


@dataclass
class Dataset:
    scans: list[np.ndarray]
    odometry: np.ndarray  # body-frame increments, first entry zero
    truth: np.ndarray | None
    walls: np.ndarray | None
    metadata: dict

    def save(self, path: Path):
        offsets = np.cumsum([0]+[len(scan) for scan in self.scans])
        np.savez_compressed(path, points=np.concatenate(self.scans), offsets=offsets,
                            odometry=self.odometry,
                            truth=self.truth if self.truth is not None else np.empty((0,3)),
                            walls=self.walls if self.walls is not None else np.empty((0,2,2)),
                            metadata=json.dumps(self.metadata))


def raycast(origin, angles, walls, max_range=12.0):
    """Intersect each ray with finite line segments; infinity means no return."""
    directions = np.column_stack((np.cos(angles), np.sin(angles)))
    starts, edges = walls[:,0], walls[:,1]-walls[:,0]
    delta = starts - origin
    cross = lambda a,b: a[...,0]*b[...,1]-a[...,1]*b[...,0]
    denominator = cross(directions[:,None,:], edges[None,:,:])
    safe = np.where(np.abs(denominator)>1e-12, denominator, np.nan)
    distance = cross(delta,edges)[None,:]/safe
    fraction = cross(delta[None,:,:], directions[:,None,:])/safe
    valid = (distance>0) & (distance<=max_range) & (fraction>=0) & (fraction<=1)
    return np.min(np.where(valid,distance,np.inf),axis=1)


def synthetic_dataset(frames=120, seed=41, beams=180) -> Dataset:
    if frames < 12 or beams < 32:
        raise ValueError('Use at least 12 frames and 32 LiDAR beams')
    rng = np.random.default_rng(seed)
    walls = []
    def box(x0,y0,x1,y1):
        corners = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
        walls.extend([[corners[i],corners[(i+1)%4]] for i in range(4)])
    box(-7,-5,7,7)
    box(-0.7,1.6,0.8,2.7)  # island inside the robot's loop
    box(4.5,-1,5.5,2.5)
    box(-5.5,2,-4.5,4.5)
    # Independent sensor/odometry noise and slightly different routes by seed.
    a,b = rng.uniform(2.8,3.2),rng.uniform(2.0,2.3)
    t = np.linspace(0,2*np.pi,frames)
    truth = np.column_stack((a*np.sin(t), b*(1-np.cos(t)), np.arctan2(b*np.sin(t),a*np.cos(t))))
    angles = np.linspace(-np.pi,np.pi,beams,endpoint=False)
    walls = np.asarray(walls,dtype=float)
    scans, odometry = [], np.zeros((frames,3))
    for i, pose in enumerate(truth):
        ranges = raycast(pose[:2],angles+pose[2],walls)
        keep = np.isfinite(ranges) & (rng.random(beams)>0.08)
        ranges = ranges[keep] + rng.normal(0,0.008,keep.sum())
        outliers = rng.random(len(ranges))<0.025
        ranges[outliers] = rng.uniform(0.4,11.5,outliers.sum())
        scans.append(np.column_stack((np.cos(angles[keep]),np.sin(angles[keep])))*ranges[:,None])
        if i:
            delta = relative(truth[i-1],pose)
            delta[:2] *= 1.03
            delta[:2] += rng.normal(0,0.006,2)
            delta[2] += 0.0025 + rng.normal(0,0.0015)
            odometry[i] = delta
    return Dataset(scans,odometry,truth,walls,{
        'kind':'synthetic-raycast-2d-lidar','seed':seed,'frames':frames,'beams':beams,
        'range_noise_sigma_m':0.008,'dropout_probability':0.08,'outlier_probability':0.025,
        'odometry_translation_scale':1.03,'odometry_heading_bias_rad_per_frame':0.0025,
        'max_range_m':12,'ground_truth_used_by_estimator':False})


def load_carmen(path: Path, *, frames=240, stride=10, start=0) -> Dataset:
    """Read legacy 180/181-beam, one-degree FLASER data (including Radish Intel).

    The first x/y/theta triple in FLASER is deliberately ignored. Only the
    explicit odom_x/odom_y/odom_theta triple supplies the motion prior.
    Nonzero front-laser offsets and newer ROBOTLASER formats are unsupported.
    """
    if frames<2 or stride<1 or start<0:
        raise ValueError('Need >=2 frames, positive stride and nonnegative start')
    opener = bz2.open if path.suffix=='.bz2' else open
    scans,poses,timestamps = [],[],[]
    laser_index = 0
    with opener(path,'rt') as stream:
        for line in stream:
            fields = line.split()
            if not fields or fields[0].startswith('#'):
                continue
            if fields[0]=='PARAM' and fields[1]=='robot_frontlaser_offset' and float(fields[2])!=0:
                raise ValueError('This demo requires a zero front-laser offset')
            if fields[0]!='FLASER':
                continue
            index = laser_index
            laser_index += 1
            if index<start or (index-start)%stride:
                continue
            count = int(fields[1])
            if count not in (180,181) or len(fields)!=count+11:
                raise ValueError('Expected 180/181-beam CARMEN FLASER records')
            ranges = np.asarray(fields[2:2+count],dtype=float)
            pose = np.asarray(fields[5+count:8+count],dtype=float)
            timestamp = float(fields[8+count])
            if not np.all(np.isfinite(pose)) or not math.isfinite(timestamp):
                raise ValueError('Nonfinite odometry or timestamp')
            if timestamps and timestamp<=timestamps[-1]:
                raise ValueError('CARMEN timestamps must increase')
            angles = np.deg2rad(np.arange(count)-90)
            keep = np.isfinite(ranges) & (ranges>.1) & (ranges<=12.)
            scans.append(np.column_stack((np.cos(angles[keep]),np.sin(angles[keep])))*ranges[keep,None])
            poses.append(pose)
            timestamps.append(timestamp)
            if len(scans)==frames:
                break
    if len(scans)!=frames:
        raise ValueError(f'Only {len(scans)} usable scans; requested {frames}')
    deltas = np.zeros((frames,3))
    for i in range(1,frames):
        deltas[i] = relative(poses[i-1],poses[i])
    return Dataset(scans,deltas,None,None,{
        'kind':'recorded-carmen-flaser','file':path.name,'file_sha256':digest(path.read_bytes()),
        'frames':frames,'start_scan':start,'stride':stride,'last_scan':start+(frames-1)*stride,
        'duration_seconds':timestamps[-1]-timestamps[0],'max_range_m':12.,
        'first_angle_deg':-90,'angular_step_deg':1,'ground_truth_available':False,
        'motion_prior':'FLASER odom_x/odom_y/odom_theta; corrected pose fields ignored'})


class PointMap:
    def __init__(self, resolution=0.08):
        self.resolution = resolution
        self.cells = {}

    def add(self, points):
        for point in points:
            key = tuple(np.floor(point/self.resolution).astype(int))
            if key not in self.cells:
                self.cells[key] = [point.copy(),1]
            else:
                entry = self.cells[key]
                entry[0] += point
                entry[1] += 1
        if len(self.cells)>100000:
            raise ValueError('Map exceeds this bounded demo\'s 100,000 voxel limit')

    def points(self, stable=False):
        minimum = 2 if stable else 1
        selected = [value[0]/value[1] for value in self.cells.values() if value[1]>=minimum]
        return np.asarray(selected,dtype=float).reshape(-1,2)


def floating_reduction(points, targets, pose):
    u = points @ rotation(pose[2]).T
    residual = u + pose[:2] - targets
    j = np.zeros((len(points),2,3))
    j[:,0,0], j[:,1,1] = 1,1
    j[:,0,2], j[:,1,2] = -u[:,1],u[:,0]
    j, r = j.reshape(-1,3),residual.ravel()
    h, g = j.T@j,j.T@r
    return np.array([h[0,2],h[1,2],h[2,2],*g,r@r,len(points)])


class HardwareReduction:
    def __init__(self, session):
        self.session = session
        self.max_error = np.zeros(8)
        self.squared_error = np.zeros(8)
        self.packets = 0

    def __call__(self, points, targets, pose):
        total = np.zeros(8)
        for start in range(0,len(points),BATCH):
            p, q = points[start:start+BATCH],targets[start:start+BATCH]
            word = pack(p,q,pose)
            actual = decode_result(self.session.request(word))
            # The oracle scores only; only hardware values enter the solve.
            error = np.abs(actual-oracle(word))
            self.max_error = np.maximum(self.max_error,error)
            self.squared_error += error**2
            self.packets += 1
            for index,field in enumerate(specification().output_fields):
                if error[index]>field.max_abs_error:
                    raise RuntimeError(f'Hardware numerical violation in {field.name}: {error[index]}')
            total += actual
        return total

    def report(self):
        rms = np.sqrt(self.squared_error/max(1,self.packets))
        fields = specification().output_fields
        return {'packets':self.packets,'max_abs_error':dict(zip(FIELD_NAMES,self.max_error.tolist())),
                'rms_error':dict(zip(FIELD_NAMES,rms.tolist())),
                'pass':all(rms[i]<=field.max_rms_error for i,field in enumerate(fields)),
                'software_fallbacks':0,'scheduled_kernel_cycles':self.packets*specification().initiation_interval}


def estimate(scans, odometry, reduction=floating_reduction, progress=None):
    """No ground truth, known walls or prebuilt map are accepted by this API."""
    from scipy.spatial import cKDTree
    if len(scans)!=len(odometry) or not len(scans):
        raise ValueError('Scan/odometry lengths must match and be nonempty')
    point_map = PointMap()
    poses = np.zeros((len(scans),3))
    point_map.add(scans[0])
    rejected, iterations = [], []
    for index in range(1,len(scans)):
        pose = compose(poses[index-1],odometry[index])
        map_points = point_map.points(stable=index>3)
        if len(map_points)<24:
            map_points = point_map.points()
        tree = cKDTree(map_points)
        scan = scans[index]
        accepted, steps = False, 0
        for step in range(12):
            distances, indices = tree.query(transform(scan,pose))
            keep = distances<0.7
            if keep.sum()<20:
                break
            keep &= distances<=np.quantile(distances[keep],0.85)
            if keep.sum()<16:
                break
            points, targets = scan[keep],map_points[indices[keep]]
            # Recenter each linearization so even a long route fits the bounded
            # accelerator. The anchor is constant during this derivative/solve.
            local_targets = targets-pose[:2]
            values = reduction(points,local_targets,np.array([0.,0.,pose[2]]))
            h,g,_ = matrices(values)
            if not np.all(np.isfinite(h)) or not np.all(np.isfinite(g)) or np.linalg.cond(h)>1e8:
                break
            delta = np.linalg.solve(h+np.diag([1e-4,1e-4,1e-3]),-g)
            delta[:2] *= min(1.,0.25/max(np.linalg.norm(delta[:2]),1e-12))
            delta[2] = np.clip(delta[2],-0.1,0.1)
            pose += delta
            pose[2] = wrap(pose[2])
            accepted,steps = True,step+1
            if np.linalg.norm(delta[:2])<2e-5 and abs(delta[2])<2e-6:
                break
        poses[index] = pose
        iterations.append(steps)
        if accepted:
            point_map.add(transform(scan,pose))
        else:
            rejected.append(index)
        if progress and (index%20==0 or index==len(scans)-1):
            progress(index,len(scans),len(point_map.cells))
    return {'poses':poses,'map':point_map.points(stable=True),'rejected_frames':rejected,'iterations':iterations}


def odometry_trajectory(increments):
    poses = np.zeros_like(increments)
    for i in range(1,len(increments)):
        poses[i] = compose(poses[i-1],increments[i])
    return poses


def trajectory_error(poses, reference):
    distance = np.linalg.norm(poses[:,:2]-reference[:,:2],axis=1)
    yaw = np.abs(wrap(poses[:,2]-reference[:,2]))
    return {'position_rmse_m':float(np.sqrt(np.mean(distance**2))),
            'position_max_m':float(distance.max()),'heading_max_deg':float(np.rad2deg(yaw.max())),
            'endpoint_position_error_m':float(distance[-1])}


def map_error(points, walls):
    """Evaluation only: distance to the nearest finite ground-truth segment."""
    edges = walls[:,1]-walls[:,0]
    delta = points[:,None,:]-walls[None,:,0,:]
    fraction = np.clip(np.sum(delta*edges,axis=2)/np.sum(edges**2,axis=1),0,1)
    distance = np.linalg.norm(delta-fraction[:,:,None]*edges,axis=2).min(axis=1)
    return {'nearest_wall_rmse_m':float(np.sqrt(np.mean(distance**2))),
            'nearest_wall_p95_m':float(np.quantile(distance,.95)),
            'points':len(points)}


def plot_report(dataset, floating, hardware, odometry, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes = plt.subplots(1,3,figsize=(17,5),layout='constrained')
    ax = axes[0]
    points = hardware['map']
    ax.scatter(points[:,0],points[:,1],s=1,c='#718096',label='Built LiDAR map',rasterized=True)
    if dataset.walls is not None:
        for wall in dataset.walls:
            ax.plot(*wall.T,c='#b0b0b0',lw=1)
    series = [(odometry,'Odometry','#d97706'),(floating['poses'],'Float SLAM','#2563eb'),
              (hardware['poses'],'Mapped RTL SLAM','#059669')]
    if dataset.truth is not None:
        series.insert(0,(dataset.truth,'Ground truth','#222222'))
    for poses,label,color in series:
        ax.plot(poses[:,0],poses[:,1],label=label,c=color,lw=1.5)
    ax.set(aspect='equal',xlabel='x (m)',ylabel='y (m)',title='Map and estimated trajectory')
    ax.legend(fontsize=8,loc='best')
    ax = axes[1]
    reference = dataset.truth if dataset.truth is not None else floating['poses']
    for poses,label,color in series:
        if poses is reference:
            continue
        ax.plot(np.linalg.norm(poses[:,:2]-reference[:,:2],axis=1),label=label,c=color)
    ax.set(xlabel='Scan',ylabel='Position error (m)',
           title='Error against ground truth' if dataset.truth is not None else 'Difference from floating baseline')
    ax.legend(fontsize=8)
    ax = axes[2]
    difference = hardware['poses']-floating['poses']
    ax.plot(1000*np.linalg.norm(difference[:,:2],axis=1),c='#059669')
    ax.set(xlabel='Scan',ylabel='Position difference (mm)',title='Hardware vs floating SLAM')
    for ax in axes:
        ax.grid(alpha=0.15)
    fig.suptitle('2D LiDAR SLAM · generated VHDL in the tracking loop',fontsize=15)
    fig.savefig(out/'trajectory.png',dpi=160)
    fig.savefig(out/'trajectory.svg')
    plt.close(fig)


def run_slam(candidate: Path, out: Path, *, frames=120, seed=41, dataset=None, server=None):
    """Freeze hardware before trajectory validation; never repair using these results."""
    if (out/'summary.json').exists():
        raise ValueError('SLAM output already contains results; choose a fresh --out directory')
    out.mkdir(parents=True,exist_ok=True)
    dataset = dataset if dataset is not None else synthetic_dataset(frames,seed)
    dataset.save(out/'dataset.npz')
    # Acceptance bounds are written before tracking starts.
    limits = {'hardware_float_max_position_m':0.05,'hardware_float_max_heading_deg':0.5,
              'synthetic_position_rmse_m':0.3,'max_rejected_fraction':0.05}
    write_json(out/'summary.json',{'accepted':False,'status':'in_progress','limits':limits})
    server = build_server(candidate,specification(),out/'cosim') if server is None else server
    server_info = json.loads((server/'server.json').read_text())
    if server_info['contract']!=specification().public_contract():
        raise ValueError('Simulator contract differs from the SLAM contract')
    if server_info['candidate_vhdl_sha256']!=digest((candidate/'candidate.vhd').read_bytes()):
        raise ValueError('Simulator does not match the requested candidate')
    start = time.monotonic()
    print(f'Floating SLAM: {len(dataset.scans)} scans',flush=True)
    floating = estimate(dataset.scans,dataset.odometry)
    print('Mapped RTL SLAM: hardware outputs now drive every pose update',flush=True)
    with HardwareSession(server,specification(),log_folder=out) as session:
        reducer = HardwareReduction(session)
        hardware = estimate(dataset.scans,dataset.odometry,reducer,
                            lambda i,n,m: print(f'  scan {i+1}/{n}, {m} map voxels, {session.requests} RTL packets',flush=True))
        kernel_report = reducer.report()
    elapsed = time.monotonic()-start
    odometry = odometry_trajectory(dataset.odometry)
    difference = trajectory_error(hardware['poses'],floating['poses'])
    metrics = {'hardware_vs_float':difference}
    if dataset.walls is not None and len(hardware['map']):
        metrics['hardware_map_vs_truth'] = map_error(hardware['map'],dataset.walls)
    if dataset.truth is not None:
        metrics.update({name:trajectory_error(poses,dataset.truth) for name,poses in [
            ('hardware_vs_truth',hardware['poses']),('float_vs_truth',floating['poses']),('odometry_vs_truth',odometry)]})
    checks = {'hardware_numerics':kernel_report['pass'] and kernel_report['packets']>0,
              'hardware_float_position':difference['position_max_m']<=limits['hardware_float_max_position_m'],
              'hardware_float_heading':difference['heading_max_deg']<=limits['hardware_float_max_heading_deg'],
              'tracking':len(hardware['rejected_frames'])/len(dataset.scans)<=limits['max_rejected_fraction']}
    if dataset.truth is not None:
        checks['trajectory_accuracy'] = metrics['hardware_vs_truth']['position_rmse_m']<=limits['synthetic_position_rmse_m']
        checks['improves_odometry'] = metrics['hardware_vs_truth']['position_rmse_m']<metrics['odometry_vs_truth']['position_rmse_m']
    summary = {'accepted':all(checks.values()),'status':'passed' if all(checks.values()) else 'failed',
               'scope':'CPU scan-to-map SLAM with generated VHDL normal equations in mapped-netlist simulation',
               'checks':checks,'limits':limits,'metrics':metrics,'dataset':dataset.metadata,
               'dataset_sha256':digest((out/'dataset.npz').read_bytes()),'kernel':kernel_report,
               'simulator':server_info,'wall_seconds_tracking_and_checks':elapsed,
               'hardware_map_points':len(hardware['map']),'rejected_frames':hardware['rejected_frames'],
               'physical_fpga_executed':False,'physical_timing_measured':False,'pose_graph_optimization':False}
    np.savez_compressed(out/'trajectories.npz',hardware=hardware['poses'],floating=floating['poses'],odometry=odometry,
                        hardware_map=hardware['map'],floating_map=floating['map'])
    plot_report(dataset,floating,hardware,odometry,out)
    write_json(out/'summary.json',summary)
    return summary
