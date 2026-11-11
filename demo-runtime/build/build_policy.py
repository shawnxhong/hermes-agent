"""Internal resource policy shared by standalone build stages."""
import json
import os


def policy():
    value=json.loads(os.environ.get('HERMES_BUILD_POLICY','{}'))
    jobs=int(value.get('jobs',2));memory=int(value.get('memory_gb',8))
    if not 1<=jobs<=8 or not 2<=memory<=64:raise ValueError('Invalid build resource limits')
    return jobs,memory,bool(value.get('no_cache',False))


def docker_build(iid,context):
    jobs,memory,no_cache=policy()
    # Legacy builder enforces these cgroup options; BuildKit can ignore them.
    os.environ['DOCKER_BUILDKIT']='0'
    return ['docker','build','--network=none','--pull=false','--memory',f'{memory}g',
            '--memory-swap',f'{memory}g','--cpu-period','100000','--cpu-quota',str(jobs*100000),
            *(['--no-cache'] if no_cache else []),'--iidfile',str(iid),str(context)]
