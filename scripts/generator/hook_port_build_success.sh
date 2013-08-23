# hook_port_build_success.sh
#
# $1 = "port_build_success"
# $2 = <jail used>
# $3 = <port tree used>
# $4 = <full path to port>

SET_port_build_success=fire_port_build_success

fire_port_build_success() {

  [ "${3}" != "potential" ] && return

  cd ${4}
  local ARG2=$(make PORTSDIR=/home/automaton/DPorts -VPKGVERSION)
  local ARG1A=$(dirname ${4})
  local ARG1B=$(basename ${ARG1A})
  local ARG1C=$(basename ${4})
  /usr/local/etc/hooks_poudriere/transfer_merge.sh "${ARG1B}/${ARG1C}" "${ARG2}"
}
