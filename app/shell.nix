{ pkgsnix ? import ./pkgs.nix
, pkgs ? pkgsnix.pkgs
, unstable ? pkgsnix.unstable
, isDocker ? false
}:

with pkgs; let
  commonBuildInputs = [
    python311
  ];

  devBuildInputs = [
    gnumake
    pipenv
    ruff
  ];

  commonShellHook = ''
  '';

  devShellHook = ''
    export PIPENV_VENV_IN_PROJECT=1
    export PIPENV_VERBOSITY=-1
    [ ! -e .venv/bin/python ] && [ -h .venv/bin/python ] && rm -r .venv
    [ ! -f .venv/bin/activate ] && pipenv sync --dev
    case $- in *i*) exec pipenv shell --fancy;; esac
  '';

  dockerShellHook = ''
    make version
  '';
in
pkgs.mkShell {
  buildInputs = commonBuildInputs ++ (if isDocker then [ ] else devBuildInputs);
  shellHook = commonShellHook + (if isDocker then dockerShellHook else devShellHook);
}
