from __future__ import with_statement
import os
from datetime import datetime
import logging

from fabric.api import *
from fabric.contrib import files

env.user = 'bhs'
env.use_ssh_config = True
env.now = datetime.now().strftime('%Y%m%d-%H%M')


def dev():
    env.hosts = ['bhs-dev']

def push_code(rev='HEAD', virtualenv=True, requirements=True, cur_date=None):
    if cur_date is None:
        cur_date = run("date +%d.%m.%y-%H:%M:%S")
    local('git archive -o /tmp/api.tar.gz '+rev)
    put('/tmp/api.tar.gz', '/tmp')
    run('mv api /tmp/latest-api-{}'.format(cur_date))
    run('mkdir api')
    with cd("api"):
        run('tar xzf /tmp/api.tar.gz')
        if virtualenv:
            if not files.exists('env'):
                run('virtualenv env')
        if requirements:
            with prefix('. env/bin/activate'):
                run('pip install -r requirements.txt')
    run('rm -rf /tmp/api-*')
    run('mv /tmp/latest-api-{} /tmp/api-{}'.format(cur_date, cur_date))

def push_conf():
    with cd("api"):
        sudo("cp conf/api-uwsgi.ini /etc/bhs/")
        sudo("rsync -rv conf/supervisor/ /etc/supervisor/")
        sudo('cp conf/bhs_api_site /etc/nginx/sites-available/bhs_api')

def deploy(bh_env=None):
    if bh_env:
        if bh_env == "infra":
            deploy_infra()
        else:
            with settings(host_string=_get_bh_env_host_string(bh_env)):
                push_code()
                restart()
    else:
        push_code()
        restart()

@hosts('bhs-infra')
def deploy_infra(reset_requirements=False):
    cur_date = run("date +%d.%m.%y-%H:%M:%S")
    if files.exists("api/env") and not reset_requirements:
        api_env_backup_path="/tmp/env-api-{}".format(cur_date)
        run("cp -r api/env/ {}/".format(api_env_backup_path))
    else:
        api_env_backup_path=None
    push_code(virtualenv=False, requirements=False, cur_date=cur_date)
    with cd("api"):
        if api_env_backup_path:
            run("mv {}/ env/".format(api_env_backup_path))
        else:
            run('virtualenv env')
        with prefix(". env/bin/activate"):
            run("pip install -r requirements.all.txt")

def test():
    with cd("api"):
        with prefix('. env/bin/activate'):
            run('py.test tests bhs_api/*.py')

def restart():
    with cd("api"):
        '''
        run("cp conf/supervisord.conf ~")
        run("kill -HUP `cat /run/bhs/supervisord.pid`")
        run("supervisorctl restart all")
        '''
        # change the ini file to use the corrent uid for bhs
        sudo("supervisorctl restart uwsgi")
        sudo("supervisorctl restart migration")

@hosts('bhs-infra')
def pull_mongo(dbname):
    if not os.path.isdir('snapshots/latest'):
        local('mkdir -p snapshots/latest')
    run('mongodump -d {}'.format(dbname))
    with cd('dump'):
        run('tar czf {0}.tgz {0}'.format(dbname))
        get('{}.tgz'.format(dbname),
            'snapshots/')
        run('rm {}.tgz'.format(dbname))
    with lcd('snapshots/latest'):
        local('tar xzvf ../{}.tgz'.format(dbname)
            )
        # delete the old db
        local('mongorestore --drop -d {0} {0}'.format(dbname))

@hosts('bhs-infra')
def update_related(db):
    with cd('api'), prefix('. env/bin/activate'):
        run('python batch_related.py --db {}'.format(db))

def _get_bh_env_host_string(bh_env):
    return {"dev": "bhs-dev",
            "infra": "bhs-infra"}[bh_env]

def set_deployed_version(bh_env, version):
    with settings(host_string=_get_bh_env_host_string(bh_env)):
        with cd("api"):
            version = version.strip().strip("v")
            run('echo "{}" > VERSION.txt'.format(version))

def get_deployed_version(bh_env):
    with settings(host_string=_get_bh_env_host_string(bh_env)):
        with cd("api"):
            run("cat VERSION.txt")

def _run_script(bh_env, script, args_str):
    with cd("api"):
        run("PYTHONPATH=. BH_ENV={} env/bin/python scripts/{}.py {}".format(bh_env, script, args_str))

def run_script(bh_env, script, args_str):
    # very important to white-list the parameters to prevent mis-use or nasty accidents
    errors = []
    if bh_env not in ["local", "dev", "prd"]:
        errors.append("Invalid env: '{}'".format(bh_env))
    if script not in ["ensure_required_metadata", "migrate"]:
        errors.append("Invalid script: '{}'".format(script))
    if len(errors) == 0:
        if bh_env == "local":
            # allows to test it locally:
            # fab "run_script:local,ensure_required_metadata,--collection places"
            local("PYTHONPATH=. python scripts/{}.py {}".format(script, args_str))
        elif bh_env in ["dev", "prd"]:
            if script == "migrate":
                get_deployed_version("infra")
                with settings(host_string="bhs-infra"):
                    _run_script(bh_env, script, args_str)
            else:
                get_deployed_version(bh_env)
                with settings(host_string=_get_bh_env_host_string(bh_env)):
                    with cd("api"):
                        run("PYTHONPATH=. env/bin/python scripts/{}.py {}".format(script, args_str))
    else:
        abort("\n".join(errors))

def fetch_file(bh_env, remote_file, local_file):
    with settings(host_string=_get_bh_env_host_string(bh_env)):
        get(remote_file, local_file)
