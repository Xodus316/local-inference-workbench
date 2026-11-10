# Offline image environment contracts

An operator can configure `build.project_environment` for `offline-project-v1`.
It describes the pinned image's capabilities using portable metadata. The
runtime does not discover capabilities by running application code. Verify the
image independently before declaring them. No environment variable values,
credentials, reference implementations or arbitrary instructions belong here.

```json
{
  "policy": "offline-project-environment-v1",
  "required_application_files": ["server/package.json", "frontend/package.json"],
  "python_modules": [],
  "tools": [
    {"name": "node", "path": "/usr/bin/node"},
    {"name": "frontend-check", "path": "/opt/tools/frontend-check"}
  ],
  "api": {
    "base_url_env": "API_BASE_URL",
    "base_url_kind": "api_root",
    "path_prefix": "/api"
  },
  "frontend_http_server": false,
  "frontend_base_url_env": "FRONTEND_BASE_URL"
}
```

Preparation canonicalizes this typed object and freezes the identical value in
the operator configuration and top-level approved `project_environment`. Changing
it invalidates approval. Every project model stage receives the complete object,
including planning, independent testing, group planning, implementation and
repairs. Normal prompt admission counts these bytes without truncation.

Required application files must appear in the independent author's complete
implementation declaration. They are not file contents and cannot include test
paths or protected/credential paths. At most 128 distinct portable paths are
accepted. Inventories contain at most 64 Python module names and 64 named tools;
tool paths are absolute POSIX paths inside the image. Total canonical metadata
is limited to 8,192 UTF-8 bytes. Extra fields are rejected.

`python_modules` and `tools` set to `null` mean the capability inventory is
unknown. Empty lists explicitly declare no additional packages or tools. The
profile's required Python interpreter and shell remain available to the frozen
unittest command. Python standard-library names and declared local Python source
modules are allowed; this does not verify their image-specific version or
platform availability. Package inventories identify import roots, not every
submodule. Tool names and exact declared paths are accepted for literal
subprocess argument lists.

An API base of kind `origin` excludes `path_prefix`; tests append the prefix and
endpoint path. A base of kind `api_root` already includes the prefix; tests append
only the endpoint path. The contract contains the environment variable's name
and meaning, never its URL value. A frontend server declaration describes a
prestarted HTTP server; `false` does not rule out offline frontend build or DOM
tools. A named frontend URL variable is unavailable when that server is absent.

Before freezing tests or starting implementation, static checks reject omitted
required application paths, direct imports missing from an explicit inventory,
unsupported literal subprocess executables, obvious duplicate API prefixes and
reads of an explicitly unavailable frontend URL variable. Diagnostics identify
the file and line. Tests are parsed, not imported or executed by these checks.
Dynamic imports, shell strings, arbitrary URL construction and other uncertain
assumptions are not guessed. The checks do not prove dependency availability,
runtime readiness, test coverage or semantic correctness. The existing bounded
fixture and planner corrections remain separate.

Omitting the environment object preserves the historical profile behavior.
Ordinary Builds do not acquire these checks or prompt inputs. An old approval
without the optional configuration field can remain compatible only while that
field is `null`; configuring an environment requires fresh approval.
