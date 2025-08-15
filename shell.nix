{}:

let
  # Update packages with `nixpkgs-update` command
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/a595dde4d0d31606e19dcec73db02279db59d201.tar.gz") { };

  pythonLibs = with pkgs; [
    stdenv.cc.cc.lib
  ];
  python' = with pkgs; (symlinkJoin {
    name = "python";
    paths = [ python313 ];
    buildInputs = [ makeWrapper ];
    postBuild = ''
      wrapProgram "$out/bin/python3.13" --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath pythonLibs}"
    '';
  });

  packages' = with pkgs; [
    python'
    esbuild
    uv
    ruff

    (writeShellScriptBin "run" ''
      python -m gunicorn web.main:app \
        --worker-class uvicorn.workers.UvicornWorker \
        --graceful-timeout 5 \
        --keep-alive 300 \
        --access-logfile -
    '')
    (writeShellScriptBin "make-bundle" ''
      # authorized.js
      HASH=$(esbuild web/static/js/authorized.js --bundle --minify | sha256sum | head -c8 ; echo "") && \
      esbuild web/static/js/authorized.js --bundle --minify --sourcemap --charset=utf8 --outfile=web/static/js/authorized.$HASH.js && \
      find web/templates -type f -exec sed -r 's|src="/static/js/authorized\..*?js"|src="/static/js/authorized.'$HASH'.js"|g' -i {} \;

      # style.css
      HASH=$(esbuild web/static/css/style.css --bundle --minify | sha256sum | head -c8 ; echo "") && \
      esbuild web/static/css/style.css --bundle --minify --sourcemap --charset=utf8 --outfile=web/static/css/style.$HASH.css && \
      find web/templates -type f -exec sed -r 's|href="/static/css/style\..*?css"|href="/static/css/style.'$HASH'.css"|g' -i {} \;
    '')
    (writeShellScriptBin "nixpkgs-update" ''
      set -e
      hash=$(
        curl --silent --location \
        https://prometheus.nixos.org/api/v1/query \
        -d "query=channel_revision{channel=\"nixpkgs-unstable\"}" | \
        grep --only-matching --extended-regexp "[0-9a-f]{40}")
      sed -i -E "s|/nixpkgs/archive/[0-9a-f]{40}\.tar\.gz|/nixpkgs/archive/$hash.tar.gz|" shell.nix
      echo "Nixpkgs updated to $hash"
    '')
  ];

  shell' = with pkgs; ''
    export TZ=UTC
    export NIX_ENFORCE_NO_NATIVE=0
    export NIX_SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt
    export SSL_CERT_FILE=$NIX_SSL_CERT_FILE
    export PYTHONNOUSERSITE=1
    export PYTHONPATH=""

    current_python=$(readlink -e .venv/bin/python || echo "")
    current_python=''${current_python%/bin/*}
    [ "$current_python" != "${python'}" ] && rm -rf .venv/

    echo "Installing Python dependencies"
    export UV_PYTHON="${python'}/bin/python"
    uv sync --frozen

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      set +o allexport
    else
      echo "Skipped loading .env file (not found)"
    fi
  '';
in
pkgs.mkShell {
  buildInputs = packages';
  shellHook = shell';
}
