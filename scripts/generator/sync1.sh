#!/bin/sh
#
# Argument cat/port
# Merges from ports -> POTENTIAL on a port-by-port basis
#

CONFFILE=/usr/local/etc/dports.conf

if [ ! -f ${CONFFILE} ]; then
   echo "Configuration file ${CONFFILE} not found"
   exit 1
fi

checkdir ()
{
   eval "MYDIR=\$$1"
   if [ ! -d ${MYDIR} ]; then
     echo "The $1 directory (${MYDIR}) does not exist."
     exit 1
  fi
}

confopts=`grep "=" ${CONFFILE}`
for opt in ${confopts}; do
   eval $opt
done

checkdir DELTA
checkdir DPORTS
checkdir FPORTS
checkdir MERGED
checkdir POTENTIAL

usage ()
{
   echo "'sync1.sh <category/port>' to generate new value in potential portree"
   exit 1
}

[ $# -ne 1 ] && usage

WORKAREA=/tmp/merge.workarea
PORT=${DELTA}/ports/${1}

# arg 1 is the original directory
get_legacy ()
{
   local PATT='amd64'
   local RET=$(cd ${1} && grep -lE ${PATT} Makefile* *\.common 2>/dev/null | sort -u)
   echo ${RET}
}

# arg 1 is the worksite directory, replacements are in place
# arg 2+ are legacy array elements
transform ()
{
   local WORK=${1}
   local item
   shift

   while [ $# -gt 0 ]; do
      item=${1}
      shift
      cat ${WORK}/${item} | sed -E \
	 -e 's|OPTIONS_DEFAULT_amd64|OPTIONS_DEFAULT_x86_64|g' \
	 -e 's|OPTIONS_DEFINE_amd64|OPTIONS_DEFINE_x86_64|g' \
	 -e 's|_ON_amd64|_ON_x86_64|g' \
	 -e 's|_OFF_amd64|_OFF_x86_64|g' \
	 -e 's|CFLAGS_amd64|CFLAGS_x86_64|g' \
         -e '/ARCH}.*(amd64|"amd64")/s|amd64|x86_64|g' \
         > ${WORK}/${item}.filtered
      mv ${WORK}/${item}.filtered ${WORK}/${item}
   done
}

fast_and_filtered ()
{
   local ORIG=${1}
   local DEST=${2}
   local LEGACY=$(get_legacy ${ORIG})

   if [ -z "${LEGACY}" ]; then
      cpdup -VV -i0 ${ORIG} ${DEST}
   else
      rm -rf ${WORKAREA}/*
      cp -pR ${ORIG}/* ${WORKAREA}/
      transform ${WORKAREA} ${LEGACY}
      cpdup -VV -i0 ${WORKAREA} ${DEST}
   fi
}

merge()
{
   local M1=${POTENTIAL}/$1
   local DP=${DELTA}/ports/$1
   local REMOVE=${DP}/diffs/REMOVE
   local MD=0
   local DDIFF=0
   local DDRAG=0
   local LEGACY

   rm -rf ${M1} ${WORKAREA}
   mkdir -p ${M1} ${WORKAREA}

   if [ "${2}" = "FAST" ]; then
      fast_and_filtered "${FPORTS}/${1}" "${M1}"
   elif [ "${2}" = "DPORT" ]; then
      cpdup -VV -i0 ${DP}/newport ${M1}
   else
      [ -f ${DP}/Makefile.DragonFly ] && MD=1
      [ -d ${DP}/dragonfly ] && DDRAG=1
      [ -d ${DP}/diffs ] && DDIFF=1
      if [ ${MD} -eq 0 -a ${DDRAG} -eq 0 -a ${DDIFF} -eq 0 ]; then
        fast_and_filtered "${FPORTS}/${1}" "${M1}"
      else
        rm -rf ${WORKAREA}/*
        cpdup -VV -i0 ${FPORTS}/${1}/ ${WORKAREA}/
        [ ${MD} -eq 1 ] && cp -p ${DP}/Makefile.DragonFly ${WORKAREA}/
        [ ${DDRAG} -eq 1 ] && cp -pr ${DP}/dragonfly ${WORKAREA}/
        if [ ${DDIFF} -eq 1 ]; then
          if [ -f ${REMOVE} ]; then
            while read line; do
              rm ${WORKAREA}/${line}
            done < ${REMOVE}
          fi
          diffs=$(find ${DP}/diffs -name \*\.diff)
          for difffile in ${diffs}; do
            patch --force --quiet -d ${WORKAREA} -i ${difffile} || echo $1
          done
          find ${WORKAREA} -type f -name \*\.orig -exec rm {} \;
        fi
        LEGACY=$(get_legacy ${WORKAREA})
        transform ${WORKAREA} ${LEGACY}
        cpdup -VV -i0 ${WORKAREA} ${M1}
      fi
   fi
   rm -rf ${WORKAREA}
}

if [ ! -d ${PORT} ]; then
	merge "${1}" "FAST"
else
	ML=$(awk '{if (FNR == 1) {print $1}}' ${PORT}/STATUS 2>/dev/null)
	if [ -z "${ML}" ]; then
		# Likely no STATUS FILE exists, consider as PORT
		merge "${1}" "PORT"
	elif [ "${ML}" = "LOCK" ]; then
		# Locked - Copy from DPorts
		mkdir -p ${POTENTIAL}/${1}
		cpdup -VV -i0 ${DPORTS}/${1} ${POTENTIAL}/${1}
	elif [ "${ML}" = "MASK" ]; then
		# clear any existing data
		rm -rf ${POTENTIAL}/${1}
	elif [ "${ML}" = "PORT" -o "${ML}" = "DPORT" ]; then
		merge "${1}" "${ML}"
	else
		echo "STATUS FILE LINE#1 CORRUPT: ${ML}"
		echo "Nothing done"
	fi
fi

[ -d ${MERGED}/${1} ] && cpdup -VV -i0 ${POTENTIAL}/${1}/ ${MERGED}/${1}/
