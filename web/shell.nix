{ isDevelopment ? true }:

let
  # Currently using nixpkgs-unstable
  # Update with `nixpkgs-update` command
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/4284c2b73c8bce4b46a6adf23e16d9e2ec8da4bb.tar.gz") { };

  libraries' = with pkgs; [
    stdenv.cc.cc.lib
  ];

  # Wrap Python to override LD_LIBRARY_PATH
  wrappedPython = with pkgs; (symlinkJoin {
    name = "python";
    paths = [
      # Enable Python optimizations when in production
      (if isDevelopment then python312 else python312.override { enableOptimizations = true; })
    ];
    buildInputs = [ makeWrapper ];
    postBuild = ''
      wrapProgram "$out/bin/python3.12" --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath libraries'}"
    '';
  });

  packages' = with pkgs; [
    wrappedPython
    esbuild
    poetry
    ruff

    # Scripts
    # -- Misc
    (writeShellScriptBin "make-version" ''
      sed -i -r "s|VERSION_DATE = '.?'|VERSION_DATE = '$(date +%y%m%d)'|g" config.py
    '')
    (writeShellScriptBin "make-bundle" ''
      chmod +w static/js static/css templates

      # authorized.js
      HASH=$(esbuild static/js/authorized.js --bundle --minify | sha256sum | head -c8 ; echo "") && \
      esbuild static/js/authorized.js --bundle --minify --sourcemap --charset=utf8 --outfile=static/js/authorized.$HASH.js && \
      find templates -type f -exec sed -i 's|src="/static/js/authorized.js"|src="/static/js/authorized.'$HASH'.js"|g' {} \;

      # style.css
      HASH=$(esbuild static/css/style.css --bundle --minify | sha256sum | head -c8 ; echo "") && \
      esbuild static/css/style.css --bundle --minify --sourcemap --charset=utf8 --outfile=static/css/style.$HASH.css && \
      find templates -type f -exec sed -i 's|href="/static/css/style.css"|href="/static/css/style.'$HASH'.css"|g' {} \;
    '')
    (writeShellScriptBin "nixpkgs-update" ''
      set -e
      hash=$(git ls-remote https://github.com/NixOS/nixpkgs nixpkgs-unstable | cut -f 1)
      sed -i -E "s|/nixpkgs/archive/[0-9a-f]{40}\.tar\.gz|/nixpkgs/archive/$hash.tar.gz|" shell.nix
      echo "Nixpkgs updated to $hash"
    '')
    (writeShellScriptBin "docker-build-push" ''
      set -e
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker push $(docker load < $(nix-build --no-out-link) | sed -En 's/Loaded image: (\S+)/\1/p')
    '')
  ];

  shell' = with pkgs; lib.optionalString isDevelopment ''
    current_python=$(readlink -e .venv/bin/python || echo "")
    current_python=''${current_python%/bin/*}
    [ "$current_python" != "${wrappedPython}" ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry env use "${wrappedPython}/bin/python"
    poetry install --no-root --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    # Development environment variables
    export PYTHONNOUSERSITE=1
    export TZ=UTC
    export INSTANCE_SECRET="development-secret"
    export TEST_ENV=1

    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      set +o allexport
    fi
  '' + lib.optionalString (!isDevelopment) ''
    make-version
    make-bundle
  '';
in
pkgs.mkShell {
  buildInputs = packages';
  shellHook = shell';
}
