# Created by: Diederik de Groot <ddegroot@talon.nl>
# $FreeBSD$

# Based on: Gerald Pfeifer <gerald@FreeBSD.org>

PORTNAME=		gdc
PORTVERSION=		9.2.0
CATEGORIES=		lang
MASTER_SITES=		GCC
DISTNAME=		gcc-${PORTVERSION}

MAINTAINER=		ibuclaw@gdcproject.org
#PORT_MAINTAINER=	ddegroot@talon.nl
COMMENT=		GDC from GNU Compiler Collection 9

LICENSE=		GPLv3 GPLv3RLE BSL
LICENSE_COMB=		multi

ONLY_FOR_ARCHS?=	aarch64 amd64 arm armv6 armv7 i386 powerpc powerpc64 sparc64
USES=			compiler cpe gmake iconv libtool makeinfo perl5 tar:xz dlang:bootstrap tar:xz

WRKSRC=			${WRKDIR}/gcc-${PORTVERSION}
PHOBOS_VERSION=		76

LIB_DEPENDS+=		libgmp.so:math/gmp
LIB_DEPENDS+=		libmpfr.so:math/mpfr
LIB_DEPENDS+=		libmpc.so:math/mpc
BUILD_DEPENDS+=		as:devel/binutils
RUN_DEPENDS+=		as:devel/binutils

.if defined(MAINTAINER_MODE)
BUILD_DEPENDS+=		runtest:misc/dejagnu
.endif

USE_BINUTILS=		yes
USE_PERL5=		build
FAVORITE_COMPILER=	gcc

#gcc_check!=		ls /usr/local/bin/gcc[6-9] /usr/bin/gcc[6-9] 2>/dev/null || true
#.if !empty(gcc_check)
#USE_GCC=		${gcc_check:[-1]:C/.*gcc//}+
#.endif

PREFIX=			${LOCALBASE}/${PKGNAME}

CPE_VENDOR=		gnu

GDC_VERSION=		${PORTVERSION}
SUFFIX=			${PORTVERSION:C/([0-9]+).*/\1/}
SSP_UNSAFE=		yes
CFLAGS:=		${CFLAGS:N-mretpoline}
CXXFLAGS:=		${CXXFLAGS:N-mretpoline}

OPTIONS_DEFINE= 	GOLD LTO SHARED QUADMATH DLANG FORCE_BOOTSTRAP
OPTIONS_DEFAULT= 	GOLD LTO SHARED QUADMATH DRUNTIME PHOBOS

OPTIONS_GROUP=		DLANG
OPTIONS_GROUP_DLANG=	DRUNTIME PHOBOS
DLANG_DESC=		D-lang related options

GOLD_DESC=		Enabled use of gold linker
GOLD_CONFIGURE_ENABLE=	gold

LTO_DESC=		Use link time optimization
LTO_CONFIGURE_ENABLE=	lto

SHARED_DESC=		Build shared libraries
SHARED_CONFIGURE_ENABLE=shared

QUADMATH_DESC=		Enable Quadmath support
QUADMATH_CONFIGURE_ENABLE=libquadmath

FORCE_BOOTSTRAP_DESC=	Force enable bootstrap build
FORCE_BOOTSTRAP_CONFIGURE_ENABLE=bootstrap

DRUNTIME_DESC=		Include d-runtime
DRUNTIME_ENABLE=	druntime

PHOBOS_DESC=		Include phobos standard library
PHOBOS_ENABLE=		phobos
DRUNTIME_IMPLIES=	DRUNTIME

OPTIONS_SUB=		yes

.if exists(/usr/lib32/libc.so)
MULTILIB_DESC=			Build support for 32-bit and 64-bit targets
OPTIONS_DEFINE_amd64+=		MULTILIB
OPTIONS_DEFINE_powerpc64+=	MULTILIB
OPTIONS_DEFAULT_amd64+=		MULTILIB
OPTIONS_DEFAULT_powerpc64+=	MULTILIB
MULTILIB_CONFIGURE_ENABLE=	multilib
USE_LDCONFIG32=			yes
.else
CONFIGURE_ARGS+=		--disable-multilib
.endif

.include <bsd.port.pre.mk>

.if ${ARCH} == powerpc64
CONFIGURE_ENV+=			UNAME_m="powerpc64"
USE_GCC?=			yes
.endif

GNU_CONFIGURE=			yes
GNU_CONFIGURE_PREFIX=		${PREFIX}
CONFIGURE_OUTSOURCE=		yes
INSTALL_TARGET=			install-strip

.if ${UID} != 0
BINMODE=	755
.endif

CONFIGURE_ARGS+=		--disable-nls
CONFIGURE_ARGS+=		--enable-gnu-indirect-function
CONFIGURE_ARGS+=		--libdir=${TARGLIB}
CONFIGURE_ARGS+=		--libexecdir=${LIBEXEC}
CONFIGURE_ARGS+=		--with-as=${LOCALBASE}/bin/as
CONFIGURE_ARGS+=		--with-ld=${LOCALBASE}/bin/ld
CONFIGURE_ARGS+=		--with-gmp=${LOCALBASE}
CONFIGURE_ARGS+=		--with-system-zlib
CONFIGURE_ARGS+=		--enable-ld
CONFIGURE_ARGS+=		--with-pkgversion="${OPSYS} Ports Collection -- DMD v2.0${PHOBOS_VERSION}.0"
CONFIGURE_ARGS+=		--enable-languages=${LANGUAGES}
CONFIGURE_ARGS+=		--prefix=${PKG_PREFIX:Q}
CONFIGURE_ARGS+=		--enable-threads=posix
CONFIGURE_ARGS+=		--enable-checking=release
#CONFIGURE_ARGS+=		--build=${BLD_TARGET}

MAKE_ARGS+=			MAKEINFOFLAGS="--no-split"
USE_LDCONFIG=			yes

LANGUAGES:=			d

TARGBIN=			${CONFIGURE_TARGET}-${PKGNAME}
.if ${PREFIX} == ${LOCALBASE}
TARGLIB=			${PREFIX}/lib/gcc${SUFFIX}
TARGLIB32=			${PREFIX}/lib32				# The version information is during post-stage:
LIBEXEC=			${PREFIX}/libexec/gcc${SUFFIX}
.else
TARGLIB=			${PREFIX}/lib
TARGLIB32=			${PREFIX}/lib32
LIBEXEC=			${PREFIX}/libexec
.endif

PLIST_SUB+=			GDC_VERSION=${GDC_VERSION} \
PLIST_SUB+=			SUFFIX=${SUFFIX}
PLIST_SUB+=			TARGBIN=${TARGBIN}

INFO=				gdc
SUB_FILES=			pkg-message
SUB_LIST+=			TARGLIB=${TARGLIB}

.if defined(MAINTAINER_MODE)
full-regression-test: build
	cd ${TEST_WRKSRC}; ${MAKE_CMD} -sk check; ${WRKSRC}/contrib/test_summary
.endif

pre-configure:
	cd ${WRKSRC} ; contrib/gcc_update --touch
	@${RM} ${WRKSRC}/gcc/*/*.info*

post-stage:
	${RM} ${STAGEDIR}${PREFIX}/man/man7/*
	${RM} -r ${STAGEDIR}${PREFIX}/lib/gcc/${BLD_TARGET}/${GCC_VERSION}/include-fixed/
	${RM} ${WRKDIR}/PLIST.lib
	(cd ${STAGEDIR}${PREFIX}/bin && ${RM} cpp gcc-ar gcc-nm gcc-ranlib gcov* x86_64-portbld-dragonfly5.7-*)
	(cd ${STAGEDIR}${PREFIX}/man/man1 && ${RM} cpp.1.gz gcc.1.gz gcov*.gz)
	(cd ${STAGEDIR}${PREFIX}/share/info && ${RM} cpp.info cppinternals.info gcc*.info libgomp.info libquadmath.info)
	${INSTALL_PROGRAM} ${WRKSRC}/gdmd ${STAGEDIR}${PREFIX}/bin

	# Add 32-bit MULTILIB libries to destination
.if (${ARCH} == amd64 || ${ARCH} == powerpc64) && ${PORT_OPTIONS:MMULTILIB}
	${MKDIR} ${STAGEDIR}${TARGLIB32}
	${MV} ${STAGEDIR}${PREFIX}/lib/lib32 ${STAGEDIR}${TARGLIB32}
.endif

	# Add target libraries and include files to packaging list.
.for x in ${TARGLIB:S/^${PREFIX}\///} ${TARGLIB32:S/^${PREFIX}\///} ${LIBEXEC:S/^${PREFIX}\///}
	cd ${STAGEDIR}${PREFIX} ; if [ -d $x ]; then \
	    ${FIND} $x -type f -o -type l >>${WRKDIR}/PLIST.lib ;\
	fi
.endfor
	cd ${WRKDIR} ; ${SED} -i -e "/PLIST.lib/ r PLIST.lib" ${TMPPLIST}

post-install:
	${RM} ${PREFIX}/bin/${PKGNAME}

.include <bsd.port.post.mk>
