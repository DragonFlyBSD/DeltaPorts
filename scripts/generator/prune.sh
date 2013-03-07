#!/bin/sh
#
# Only prune with a freshly created MERGE area.
# So "rm -rf $MERGE/* before running merge.sh
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

usage ()
{
   echo "'prune.sh scan' to preview what will be pruned."
   echo "'prune.sh confirm' to prune and commit changes."
   exit 1
}

if [ $# -eq 1 ]; then
    case ${1} in
        scan)    CONFIRM=0 ;;
        confirm) CONFIRM=1 ;;
        *) usage ;;
    esac
else
    usage
fi

kill_directory ()
{
   port=${1}

   if [ ${CONFIRM} -eq 1 ]; then
      echo "Pruning from DPorts: ${port}"
      cd ${DPORTS} && git rm -rf ${port}
      echo "  ${port}" >> ${CMDP}
   else
      echo "Target for pruning: ${port}"
   fi
}

kill_deltaport ()
{
   port=${1}
   if [ ${CONFIRM} -eq 1 ]; then
      echo "Pruning from DeltaPorts: ${port}"
      commitmsg="Pruning: ${port} removed"
      cd ${DELTA}/ports && git rm -rf ${port}
      echo "  ${port}" >> ${CMDL}
   else
      echo "Delta Target for pruning: ${port}"
   fi
}

EXCLUDE="^(Templates/|Tools/|Mk/)"
PRLIST=/tmp/prune-merge.list
PDLIST=/tmp/prune-delta.list
CMDP=/tmp/commit.msg.dport
CMDL=/tmp/commit.msg.delta
CMMT=/tmp/commit.msg

rm -f ${CMDP} ${CMDL}
portdirs=$(cd ${DPORTS}; find * -type d -depth 1 -maxdepth 1 | sort | grep -vE ${EXCLUDE})
dtdirs=$(cd ${DELTA}/ports; find * -type d -depth 1 -maxdepth 1 | sort)

# Prune DPorts first
cd ${MERGED}
find * -type d -depth 1 -maxdepth 1 | grep -vE ${EXCLUDE} | sort > ${PRLIST}
for portdir in ${portdirs}; do
    LOOK=$(look -t "\n" "${portdir}" ${PRLIST})
    [ -z "${LOOK}" ] && kill_directory "${portdir}"
done
rm ${PRLIST}

if [ -f ${CMDP} ]; then
    NUMLINES=$(cat ${CMDP} | wc -l)
    printf "Prune %d obsolete ports\n\n" ${NUMLINES} > ${CMMT}
    echo "FreeBSD Ports maintainers have already deleted the following:" >> ${CMMT}
    cat ${CMDP} >> ${CMMT}
    cd ${DPORTS} && git commit -f ${CMMT}
    rm ${CMDP} ${CMMT}
fi

# Check if Delta port entry is still current
cd ${FPORTS}
find * -type d -depth 1 -maxdepth 1 | grep -vE ${EXCLUDE} | sort > ${PDLIST}
for portdir in ${dtdirs}; do
    LOOK=$(look -t "\n" "${portdir}" ${PDLIST})
    if [ -z "${LOOK}" ]; then
        SFILE=${DELTA}/ports/${portdir}/STATUS
        # if no SFILE, conservatively skip.  it may be in work
        if [ -f ${SFILE} ]; then
            MASK=$(grep -E "^(MASK|PORT)" ${SFILE})
            [ -n "${MASK}" ] && kill_deltaport "${portdir}"
        else
            echo "Note: ${portdir} has no STATUS file"
        fi
    fi
done
rm ${PDLIST}

if [ -f ${CMDL} ]; then
    NUMLINES=$(cat ${CMDL} | wc -l)
    printf "Prune %d obsolete ports\n\n" ${NUMLINES} > ${CMMT}
    echo "FreeBSD Ports maintainers have already deleted the following:" >> ${CMMT}
    cat ${CMDL} >> ${CMMT}
    cd ${DELTA}/ports && git commit -f ${CMMT} .
    rm ${CMDL} ${CMMT}
fi
