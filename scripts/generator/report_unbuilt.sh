#!/bin/sh
#
# This script compares the DPORTS entries to the packages
# created and generates a report listing all ports that did
# not generate a package.
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

checkdir DPORTS
checkdir FPORTS

usage ()
{
   echo "command is 'report_unbuilt <jail-porttree>'"
   exit 1
}

if [ $# -ne 1 ]; then
    usage
fi


EXCLUDE="^(Templates/|Tools/|Mk/|Keywords/)"
PKGDIR=/build/boomdata/data/packages/${1}/All

if [ ! -d ${PKGDIR} ]; then
    echo "The package directory does not exist"
    exit 1
fi

portdirs=$(cd ${DPORTS}; find * -type d -depth 1 -maxdepth 1 | sort | grep -vE ${EXCLUDE})
for portdir in ${portdirs}; do

    case ${portdir} in
	french/aster | games/crafty-tablebase*)
	    continue ;;
    esac
    PN=$(make -C ${DPORTS}/${portdir} PORTSDIR=${DPORTS} LOCALBASE=/usr/dummy PYTHON_DEFAULT_VERSION=2.7 USE_TCL=86 -V PKGNAME).txz
    FULLPATH=${PKGDIR}/${PN}
    if [ ! -f "${FULLPATH}" ]; then
	REASON=$(make -C ${MERGED}/${portdir} PORTSDIR=${MERGED} -V IGNORE)
	if [ -z "${REASON}" ]; then
	        echo ${portdir}
	else
		echo "${portdir}	${REASON}"
	fi
    fi
done
