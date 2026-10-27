# Functions shared by the shell scripts of the repository (sourced, never run): ./init_dev_venv.sh and tools/*.sh.
#
# The scripts run with the bash 3.2 of macOS (the last GPLv2 release, still the default there) and in the Git Bash
# of Windows: no bash 4 feature (${var,,}, ${var^^}, declare -A, mapfile...), no GNU only option (readlink -f, sed -i
# without suffix...). tools/check_scripts.sh checks it in the CI.

# venv_bin VENV_DIR: the directory of the executables of a venv, bin/ on Linux and macOS, Scripts/ on Windows
venv_bin() {
    if [[ -d "$1/Scripts" && ! -d "$1/bin" ]]; then echo "$1/Scripts"; else echo "$1/bin"; fi
}
