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
  ] ++ lib.optionals isDevelopment [
    # Development packages
    poetry
    ruff
  ];

  shell' = with pkgs; ''
    export PROJECT_DIR="$(pwd)"
  '' + lib.optionalString isDevelopment ''
    [ ! -e .venv/bin/python ] && [ -h .venv/bin/python ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry install --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    export LD_LIBRARY_PATH="${lib.makeLibraryPath libraries'}"

    # Development environment variables
    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      +o allexport
    fi
  '';
in
pkgs.mkShell
{
  libraries = libraries';
  buildInputs = libraries' ++ packages';
  shellHook = shell';
}
