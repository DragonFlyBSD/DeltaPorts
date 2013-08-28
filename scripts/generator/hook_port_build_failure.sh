# hook_port_build_failure.sh
#
#  $1 = "port_build_failure"
#  $2 = <jail used>
#  $3 = <port tree used>
#  $4 = <full path to port>
#  $5 = <failed in stage>

SET_port_build_failure=fire_port_build_failure

fire_port_build_failure() {
  [ "${3}" != "potential" ] && return

    cd ${4}
    local ARG2=$(make PORTSDIR=/home/automaton/DPorts -VPKGVERSION)
    local ARG1A=$(dirname ${4})
    local ARG1B=$(basename ${ARG1A})
    local ARG1C=$(basename ${4})
    local BASE=/usr/local/poudriere/data/logs/bulk/${2}/${3}

    /usr/local/etc/hooks_poudriere/failure_status.sh "${ARG1B}/${ARG1C}" "${ARG2}" "${BASE}"
}
