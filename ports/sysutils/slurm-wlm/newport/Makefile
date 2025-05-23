PORTNAME=	slurm
DISTVERSION=	20.02.7
PORTREVISION=	3
CATEGORIES=	sysutils python
MASTER_SITES=	https://ftp.icm.edu.pl/packages/slurm/
PKGNAMESUFFIX=	-wlm

MAINTAINER=	ports@FreeBSD.org
COMMENT=	Simple Linux Utility for Resource Management
WWW=		https://slurm.schedmd.com/

LICENSE=	GPLv2+
LICENSE_FILE=	${WRKSRC}/COPYING

NOT_FOR_ARCHS=		armv6 armv7 i386 mips powerpc powerpcspe
NOT_FOR_ARCHS_REASON=	32-bit support is deprecated

LIB_DEPENDS=	libjson-c.so:devel/json-c \
		libsysinfo.so:devel/libsysinfo \
		libmunge.so:security/munge \
		liblz4.so:archivers/liblz4

USES=		compiler:c11 cpe gmake gnome libtool localbase lua perl5 \
		pkgconfig python:build readline shebangfix ssl tar:bz2
USE_PERL5=	build
CPE_VENDOR=	schedmd
USE_LDCONFIG=	yes
USE_RC_SUBR=	slurmctld slurmd

USERS=		slurm
GROUPS=		${USERS}

GNU_CONFIGURE=	yes
INSTALL_TARGET=	install-strip
PLIST_SUB=	PORTVERSION="${PORTVERSION}"
SHEBANG_FILES=	doc/html/shtml2html.py doc/man/man2html.py

OPTIONS_DEFINE=		CURL DOCS GUI HDF5 HWLOC IPMI MYSQL RRD
OPTIONS_DEFAULT=	HDF5 HWLOC
OPTIONS_SUB=		yes

CURL_DESC=	Require cURL for elasticsearch plugins
GUI_DESC=	Build sview GUI config tool
HDF5_DESC=	Job profiling using HDF5
HWLOC_DESC=	Portable hardware locality
IPMI_DESC=	IPMI energy consumption accounting
RRD_DESC=	RRD external sensor data collection

DOCS_BUILD_DEPENDS=	man2html:textproc/man2html

CURL_LIB_DEPENDS=	libcurl.so:ftp/curl
CURL_CONFIGURE_WITH=	libcurl

GUI_USE=		GNOME=cairo,gdkpixbuf2,glib20,gtk20
GUI_LIB_DEPENDS=	libfontconfig.so:x11-fonts/fontconfig \
			libfreetype.so:print/freetype2
GUI_USES=		gettext
GUI_CONFIGURE_ENABLE=	glibtest gtktest x11
# Neither of these work.  See post-patch below.
#GTK2_CONFIGURE_ENV_OFF=	ac_gtk_test=no
#GTK2_CONFIGURE_OFF=	--disable-gtktest

HDF5_LIB_DEPENDS=	libhdf5_cpp.so:science/hdf5 \
			libsz.so:science/libaec
HDF5_CONFIGURE_WITH=	hdf5
HDF5_CONFIGURE_ENV=	H5CC=${LOCALBASE}/bin/h5cc

HWLOC_LIB_DEPENDS=	libhwloc.so.15:devel/hwloc2
HWLOC_CONFIGURE_WITH=	hwloc

IPMI_LIB_DEPENDS=	libfreeipmi.so:sysutils/freeipmi
IPMI_CONFIGURE_WITH=	freeipmi

MYSQL_USES=		mysql
MYSQL_CONFIGURE_OFF=	--with-mysql_config=/nonexistent

RRD_LIB_DEPENDS=	librrd.so:databases/rrdtool
RRD_CONFIGURE_WITH=	rrdtool

# --docdir is non-functional
# CONFIGURE_ARGS=	--docdir=${DOCSDIR}
# FreeBSD Infiniband support is still experimental
# slurmrestd does not build on FreeBSD
CONFIGURE_ARGS=	--without-ofed --disable-slurmrestd
DOCSDIR=	${PREFIX}/share/doc/${PORTNAME}-${DISTVERSION}

CFLAGS+=	-I${WRKSRC}/slurm -I${LOCALBASE}/include -fcommon
LDFLAGS+=	-L${LOCALBASE}/lib -lsysinfo -fcommon

# Hack around nonfunctional --disable-gtktest flag
pre-configure-GUI-off:
	@${REINPLACE_CMD} -e 's|min_gtk_version=.*|min_gtk_version=2000.0.0|' \
		${WRKSRC}/configure

# SLURM's configure enables interactive jobs if pty.h exists.  Replacing
# #include <pty.h> with appropriate headers will therefore not work, so instead
# add a pty.h for the build.
pre-configure:
	@${CP} ${FILESDIR}/pty.h ${WRKSRC}/slurm
	@${REINPLACE_CMD} -e 's|sched_setaffinity|cpuset_setaffinity|g' \
		-e 's|%%LOCALBASE%%|${LOCALBASE}|' ${WRKSRC}/configure

	@${REINPLACE_CMD} -e 's|"/proc|"/compat/linux/proc|g' \
		-e 's|(/proc)|(/compat/linux/proc)|g' \
		${WRKSRC}/src/slurmd/slurmstepd/req.c \
		${WRKSRC}/src/slurmd/slurmstepd/pdebug.c \
		${WRKSRC}/src/slurmd/common/xcpuinfo.c \
		${WRKSRC}/src/slurmd/common/xcgroup.c \
		${WRKSRC}/src/slurmd/common/set_oomadj.c \
		${WRKSRC}/src/slurmd/common/proctrack.c \
		${WRKSRC}/src/common/callerid.c \
		${WRKSRC}/src/plugins/task/affinity/affinity.c \
		${WRKSRC}/src/plugins/acct_gather_energy/rapl/acct_gather_energy_rapl.c \
		${WRKSRC}/src/plugins/jobacct_gather/cgroup/jobacct_gather_cgroup.c \
		${WRKSRC}/src/plugins/jobacct_gather/common/common_jag.c \
		${WRKSRC}/src/plugins/jobacct_gather/linux/jobacct_gather_linux.c \
		${WRKSRC}/src/plugins/switch/cray_aries/scaling.c \
		${WRKSRC}/src/plugins/proctrack/cray_aries/proctrack_cray_aries.c \
		${WRKSRC}/src/plugins/proctrack/cgroup/proctrack_cgroup.c \
		${WRKSRC}/src/plugins/proctrack/pgid/proctrack_pgid.c \
		${WRKSRC}/src/plugins/proctrack/linuxproc/kill_tree.c \
		${WRKSRC}/src/plugins/acct_gather_filesystem/lustre/acct_gather_filesystem_lustre.c \
		${WRKSRC}/src/plugins/node_features/knl_generic/node_features_knl_generic.c \
		${WRKSRC}/contribs/cray/pam_job.c

	@if ${WHICH} sbatch > /dev/null; then \
		${ECHO_CMD} "********************************************"; \
		${ECHO_CMD} "Build may fail while slurm-wlm is installed."; \
		${ECHO_CMD} "Deinstall slurm-wlm and run make again."; \
		${ECHO_CMD} "********************************************"; \
		false; \
	fi

post-install:
	${INSTALL_DATA} ${WRKSRC}/etc/slurm.conf.example \
		${STAGEDIR}${PREFIX}/etc/slurm.conf.sample

.include <bsd.port.mk>
