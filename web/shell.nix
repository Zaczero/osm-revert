{ isDevelopment ? true }:

let
  # Currently using nixpkgs-23.11-darwin
  # Get latest hashes from https://status.nixos.org/
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/66b333426654b140717cc5594a7210c4b95890f5.tar.gz") { };

  libraries' = with pkgs; [
    # Base libraries
    stdenv.cc.cc.lib
  ];

  packages' = with pkgs; [
    # Base packages
    python312
    esbuild

    # Scripts
    # -- Misc
    (writeShellScriptBin "make-version" ''
      sed -i -r "s|VERSION_DATE = '.?'|VERSION_DATE = '$(date +%Y%m%d)'|g" config.py
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
  ] ++ lib.optionals isDevelopment [
    # Development packages
    poetry
    ruff

    # Scripts
    # -- Misc
    (writeShellScriptBin "docker-build-push" ''
      set -e
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker push $(docker load < $(nix-build --no-out-link) | sed -En 's/Loaded image: (\S+)/\1/p')
    '')
  ];

  shell' = with pkgs; ''
    export PROJECT_DIR="$(pwd)"
  '' + lib.optionalString isDevelopment ''
    [ ! -e .venv/bin/python ] && [ -h .venv/bin/python ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry install --no-root --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    export LD_LIBRARY_PATH="${lib.makeLibraryPath libraries'}"

    # Development environment variables
    export INSTANCE_SECRET="development-secret"

    if [ -f .env ]; then
      set -o allexport
      source .env set
      +o allexport
    fi
  '' + lib.optionalString (!isDevelopment) ''
    make-version
    make-bundle
  '';
in
pkgs.mkShell
{
  libraries = libraries';
  buildInputs = libraries' ++ packages';
  shellHook = shell';
}
