{ isDevelopment ? true }:

let
  # Currently using nixpkgs-unstable
  # Update with `nixpkgs-update` command
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/7872526e9c5332274ea5932a0c3270d6e4724f3b.tar.gz") { };

  libraries' = with pkgs; [
    # Base libraries
    stdenv.cc.cc.lib
  ];

  # Wrap Python to override LD_LIBRARY_PATH
  wrappedPython = with pkgs; (symlinkJoin {
    name = "python";
    paths = [ python312 ];
    buildInputs = [ makeWrapper ];
    postBuild = ''
      wrapProgram "$out/bin/python3.12" --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath libraries'}"
    '';
  });

  packages' = with pkgs; [
    # Base packages
    wrappedPython
  ] ++ lib.optionals isDevelopment [
    # Development packages
    poetry
    ruff

    (writeShellScriptBin "nixpkgs-update" ''
      set -e
      hash=$(git ls-remote https://github.com/NixOS/nixpkgs nixpkgs-unstable | cut -f 1)
      sed -i -E "s|/nixpkgs/archive/[0-9a-f]{40}\.tar\.gz|/nixpkgs/archive/$hash.tar.gz|" shell.nix
      echo "Nixpkgs updated to $hash"
    '')
  ];

  shell' = with pkgs; lib.optionalString isDevelopment ''
    current_python=$(readlink -e .venv/bin/python || echo "")
    current_python=''${current_python%/bin/*}
    [ "$current_python" != "${wrappedPython}" ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry env use "${wrappedPython}/bin/python"
    poetry install --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    # Development environment variables
    export PYTHONNOUSERSITE=1

    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      +o allexport
    fi
  '';
in
pkgs.mkShell {
  buildInputs = packages';
  shellHook = shell';
}
