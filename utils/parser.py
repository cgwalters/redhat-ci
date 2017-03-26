#!/usr/bin/env python3

import os
import sys
import yaml
import shlex

import utils.common as common

from pykwalify.core import Core
from pykwalify.errors import SchemaError


class SuiteParser:

    def __init__(self, filepath):
        self.contexts = []
        self.met_required = False
        self.filepath = filepath

    def parse(self):
        "Generator of testsuites parsed from the given YAML file."

        suite = None
        with open(self.filepath) as f:
            for idx, raw_yaml in enumerate(yaml.safe_load_all(f.read())):
                try:
                    suite = self._merge(suite, raw_yaml)
                    self._validate(suite)
                    yield dict(suite)
                except SchemaError as e:
                    # if it happens on the very first document, let's
                    # just give the exact error directly
                    if idx == 0:
                        raise e
                    raise SchemaError("failed to parse %s testsuite"
                                      % common.ordinal(idx + 1)) from e

    def _merge(self, suite, new):
        "Merge the next document into the current one."

        if type(new) is not dict:
            raise SyntaxError("top-level type should be a dict")

        if suite is None:

            # The 'context' key is special. It's optional on the
            # first suite (defaulting to 'Red Hat CI'), but
            # required on subsequent suites.
            if 'context' not in new:
                new['context'] = "Red Hat CI"

        if 'inherit' in new and type(new['inherit']) is not bool:
            raise SyntaxError("expected 'bool' value for 'inherit' key")

        # if we're not inheriting, then let's just return the new suite itself
        if suite is None or not new.get('inherit', False):
            return self._normalize(new.copy())

        assert type(suite) is dict

        # if the suite specifies an envtype, then make sure we
        # don't inherit the envtype of the old one
        envtypes = ['container', 'host', 'cluster']
        if any([i in new for i in envtypes]):
            for i in envtypes:
                if i in suite:
                    del suite[i]

        # we always expect a new context key
        del suite['context']

        suite.update(new)

        return self._normalize(suite)

    def _normalize(self, suite):
        for k, v in list(suite.items()):
            if k == 'inherit' or v is None:
                del suite[k]
        return suite

    def _validate(self, suite):

        schema = os.path.join(sys.path[0], "utils/schema.yml")
        ext = os.path.join(sys.path[0], "utils/ext_schema.py")
        c = Core(source_data=suite, schema_files=[schema], extensions=[ext])
        c.validate()

        if suite['context'] in self.contexts:
            raise SchemaError("duplicate 'context' value detected")

        self.met_required = self.met_required or suite.get('required', False)

        if suite['context'] == "required" and self.met_required:
            raise SchemaError('context "required" forbidden when using the '
                              "'required' key")

        self.contexts.append(suite['context'])


def _write_to_file(dir, fn, s):
    with open(os.path.join(dir, fn), 'w') as f:
        f.write(s)


def _flush_host(host, outdir):
    if 'ostree' in host:
        val = host['ostree']
        assert type(val) in [str, dict]
        if type(val) is str:
            assert val == "latest"
            _write_to_file(outdir, "ostree_revision", "")
        else:
            _write_to_file(outdir, "ostree_remote", val.get('remote', ''))
            _write_to_file(outdir, "ostree_branch", val.get('branch', ''))
            _write_to_file(outdir, "ostree_revision", val.get('revision', ''))
    val = host.get("specs", {})
    _write_to_file(outdir, "min_ram", str(val.get("ram", 2048)))
    _write_to_file(outdir, "min_cpus", str(val.get("cpus", 1)))
    _write_to_file(outdir, "min_disk", str(val.get("disk", 20)))
    _write_to_file(outdir, "min_secondary_disk",
                   str(val.get("secondary-disk", 0)))
    _write_to_file(outdir, "distro", host['distro'])


def flush_suite(suite, outdir):

    os.makedirs(outdir)

    if 'host' in suite:
        dir = os.path.join(outdir, "host")
        os.mkdir(dir)
        _flush_host(suite['host'], dir)
        _write_to_file(outdir, 'envtype', 'host')
        _write_to_file(outdir, 'controller', 'host')

    if 'container' in suite:
        _write_to_file(outdir, "image", suite['container']['image'])
        _write_to_file(outdir, 'envtype', 'container')
        _write_to_file(outdir, 'controller', 'container')

    if 'cluster' in suite:
        cluster = suite['cluster']
        for i, host in enumerate(cluster['hosts']):
            dir = os.path.join(outdir, "host-%d" % i)
            os.mkdir(dir)
            _flush_host(host, dir)
            _write_to_file(dir, "name", host['name'])
        _write_to_file(outdir, 'nhosts', str(i+1))
        if 'container' in cluster:
            _write_to_file(outdir, "image", cluster['container']['image'])
            _write_to_file(outdir, 'controller', 'container')
        else:
            _write_to_file(outdir, 'controller', 'host')
        _write_to_file(outdir, 'envtype', 'cluster')

    if 'tests' in suite:
        _write_to_file(outdir, "tests", '\n'.join(suite['tests']))

    _write_to_file(outdir, "branches",
                   '\n'.join(suite.get('branches', ['master'])))

    timeout = common.str_to_timeout(suite.get('timeout', '2h'))
    _write_to_file(outdir, "timeout", str(timeout))

    _write_to_file(outdir, "context", suite.get('context'))

    if 'extra-repos' in suite:
        repos = ''
        for repo in suite['extra-repos']:
            repos += "[%s]\n" % repo['name']
            for key, val in repo.items():
                repos += "%s=%s\n" % (key, val)
        if repos != "":
            _write_to_file(outdir, "rhci-extras.repo", repos)

    if 'packages' in suite:
        packages = []
        for pkg in suite['packages']:
            packages.append(shlex.quote(pkg))
        _write_to_file(outdir, "packages", ' '.join(packages))

    if 'artifacts' in suite:
        _write_to_file(outdir, "artifacts", '\n'.join(suite['artifacts']))

    if 'env' in suite:
        envs = ''
        for k, v in suite['env'].items():
            envs += 'export %s="%s"\n' % (k, v)
        _write_to_file(outdir, "envs", envs)

    if 'build' in suite:
        v = suite['build']
        if type(v) is bool and v:
            _write_to_file(outdir, "build", '')
        elif type(v) is dict:
            _write_to_file(outdir, "build", '')
            _write_to_file(outdir, "build.config_opts",
                           v.get('config-opts', ''))
            _write_to_file(outdir, "build.build_opts",
                           v.get('build-opts', ''))
            _write_to_file(outdir, "build.install_opts",
                           v.get('install-opts', ''))
