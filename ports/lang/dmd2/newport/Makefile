# Created by: Diederik de Groot <ddegroot@talon.nl>
# $FreeBSD: head/lang/dmd2/Makefile 498264 2019-04-07 07:43:37Z antoine $

PORTNAME=		dmd
PORTVERSION=		${DMD_VERSION}
DISTVERSIONPREFIX=	v
CATEGORIES=		lang
PKGNAMESUFFIX=		2

MAINTAINER=		cy@FreeBSD.org
COMMENT=		D 2.0 compiler

LICENSE=		ART10 BSL
LICENSE_COMB=		multi

NOT_FOR_ARCHS=		aarch64 armv6 armv7 mips64 powerpc64 sparc64
NOT_FOR_ARCHS_REASON=	dmd only supports i386 and x86_64, use ldc or gdc instead

BUILD_DEPENDS=		bash:shells/bash

USES=			gmake compiler dlang:bootstrap

# dcompiler arguments/variables
DRT_MINVER=             20791
DRT_MAXVER=             20881

DMD_MAJOR=		2
DMD_MINOR=		0
DMD_PATCHLEVEL=		89
DMD_REV=		0
DMD_VERSION=		${DMD_MAJOR}.${DMD_MINOR}${DMD_PATCHLEVEL}.${DMD_REV}
DMD_LIBVER=		0.${DMD_PATCHLEVEL}.${DMD_REV}

USE_GITHUB=		nodefault
GH_ACCOUNT=		dlang
GH_PROJECT=		dmd:dmd druntime:druntime phobos:phobos

# DOCS should be build from github.com/dlang/dlang.org in a seperate package
# because it is dependent on lang/dlang-tools:rdmd
OPTIONS_DEFINE=		EXAMPLES LTO FORCE_BOOTSTRAP SHARED
OPTIONS_DEFAULT=	EXAMPLES LTO SHARED
OPTIONS_SUB=		yes

LTO_DESC=		Enable Link Time Optimizations
FORCE_BOOTSTRAP_DESC=	Override search for suitable bootstrap d-compiler
FORCE_BOOTSTRAP_BUILD_DEPENDS=${LOCALBASE}/bin/ldmd2:lang/ldc
SHARED_DESC=		Enable shared libraries

PREFIX?=		${LOCALBASE}/dmd2
MAKEFILE=		posix.mak
EXAMPLESDIR=		${PREFIX}/share/examples/dmd2
DOCSDIR=		${PREFIX}/share/doc/dmd2
WRKSRC=			${WRKDIR}
MODULEDIR=		${PREFIX}/include/d

PLIST_SUB+=		DMD_PATCHLEVEL=${DMD_PATCHLEVEL} \
			DMD_LIBVER=${DMD_LIBVER} \

.include <bsd.port.options.mk>

.if ${OPSYS} == FreeBSD && ${OSVERSION} >= 1200031
EXTRA_PATCHES=		${PATCHDIR}/ino64-*
.endif

OSMODEL?=		${OPSYS:tl}

MAKE_ARGS+=		SHELL=${LOCALBASE}/bin/bash HOST_DMD=${DCOMPILER_DMD}

.if ${ARCH} == amd64 || ${ARCH} == x86_64
MAKE_ARGS+=		MODEL=64 PIC=1
MODEL=			64
CFLAGS+=		-fPIC
.else
MAKE_ARGS+=		MODEL=32
MODEL=			32
.endif

.if ${PORT_OPTIONS:MFORCE_BOOTSTRAP}
DCOMPILER_DMD=		${LOCALBASE}/bin/ldmd2
.endif

.if ${PORT_OPTIONS:MSHARED}
MAKE_ARGS+=		SHARED=1
.endif

.if ${PORT_OPTIONS:MLTO}
MAKE_ARGS+=		ENABLE_LTO=1
.endif

.if defined(WITH_DEBUG)
MAKE_ARGS+=		ENABLE_DEBUG=1 DEBUG_FLAGS=-g -DDEBUG=1 -DUNITTEST BUILD=debug
.else
MAKE_ARGS+=		ENABLE_RELEASE=1 BUILD=release
.endif

post-extract:
	@${MV} ${WRKSRC_dmd} ${WRKSRC}/dmd
	@${MV} ${WRKSRC_druntime} ${WRKSRC}/druntime
	@${MV} ${WRKSRC_phobos} ${WRKSRC}/phobos

post-patch:
	@${REINPLACE_CMD} -e "s|g++|${CXX}|" \
			  -e "s|/etc|${PREFIX}/etc|" \
		${WRKSRC}/dmd/src/posix.mak
	@${REINPLACE_CMD} -e "s|cc|${CC}|" ${WRKSRC}/phobos/posix.mak
	@${REINPLACE_CMD} -e "s|/etc|${PREFIX}/etc|"	\
			  -e "s|\(dmd\)|\12|gI"		\
			  -e "s|\\\BR|.BR|"		\
		${WRKDIR}/dmd/docs/man/man5/dmd.conf.5
	@${REINPLACE_CMD} -e "s|%@P%/../../src/phobos|${MODULEDIR}/phobos|"	\
			  -e "s|%@P%/../../src/druntime|${MODULEDIR}/druntime|"	\
			  -e "s|%@P%/../lib32|${PREFIX}/lib|"			\
			  -e "s|%@P%/../lib64|${PREFIX}/lib|"			\
			  ${WRKDIR}/dmd/ini/${OSMODEL}/bin${MODEL}/dmd.conf
	@${ECHO_CMD} >> ${WRKDIR}/dmd/ini/${OSMODEL}/bin${MODEL}/dmd.conf

make-phobos: make-druntime
	cd ${WRKSRC}/phobos && ${MAKE_ENV} ${MAKE_CMD} -f ${MAKEFILE} ${MAKE_ARGS} ${BUILD}

make-druntime: make-dmd
	cd ${WRKSRC}/druntime && ${MAKE_ENV} ${MAKE_CMD} -f ${MAKEFILE} ${MAKE_ARGS} ${BUILD}

make-dmd:
	cd ${WRKSRC}/dmd && ${MAKE_ENV} ${MAKE_CMD} -f ${MAKEFILE} -j${MAKE_JOBS_NUMBER} ${MAKE_ARGS} ${BUILD}

do-build: make-dmd make-phobos make-druntime

do-install:
	${INSTALL_MAN} ${WRKDIR}/dmd/docs/man/man5/dmd.conf.5 ${STAGEDIR}${MANPREFIX}/man/man5/dmd.conf.5
	${INSTALL_DATA} ${WRKSRC}/dmd/generated/${OSMODEL}/release/${MODEL}/dmd.conf ${STAGEDIR}${PREFIX}/etc/dmd.conf
	${INSTALL_PROGRAM} ${WRKSRC}/dmd/generated/${OSMODEL}/release/${MODEL}/dmd ${STAGEDIR}${PREFIX}/bin/dmd
	${INSTALL_DATA} ${WRKSRC}/phobos/generated/${OSMODEL}/release/${MODEL}/libphobos2.a ${STAGEDIR}${PREFIX}/lib/libphobos2.a
.if ${PORT_OPTIONS:MSHARED}
	${INSTALL_LIB} ${WRKSRC}/phobos/generated/${OSMODEL}/release/${MODEL}/libphobos2.so.${DMD_LIBVER} ${STAGEDIR}${PREFIX}/lib/libphobos2.so.${DMD_LIBVER}
	${RLN} ${STAGEDIR}${PREFIX}/lib/libphobos2.so.${DMD_LIBVER} ${STAGEDIR}${PREFIX}/lib/libphobos2.so.0.${DMD_PATCHLEVEL}
	${RLN} ${STAGEDIR}${PREFIX}/lib/libphobos2.so.${DMD_LIBVER} ${STAGEDIR}${PREFIX}/lib/libphobos2.so
.endif
	${INSTALL_DATA} ${WRKDIR}/dmd/ini/${OSMODEL}/bin${MODEL}/dmd.conf ${STAGEDIR}${PREFIX}/etc/dmd.conf.sample
	cd ${WRKDIR}/druntime/import && ${COPYTREE_SHARE} . ${STAGEDIR}${MODULEDIR}/druntime/import
	cd ${WRKSRC}/phobos && ${COPYTREE_SHARE} std ${STAGEDIR}${MODULEDIR}/phobos
	cd ${WRKSRC}/phobos && ${COPYTREE_SHARE} etc ${STAGEDIR}${MODULEDIR}/phobos

post-install-EXAMPLES-on:
	${MKDIR} ${STAGEDIR}${EXAMPLESDIR}
	cd ${WRKDIR}/dmd && ${COPYTREE_SHARE} samples ${STAGEDIR}${EXAMPLESDIR}
	${INSTALL_DATA} ${WRKDIR}/dmd/ini/${OSMODEL}/bin32/dmd.conf ${STAGEDIR}${EXAMPLESDIR}/dmd32.conf
	${INSTALL_DATA} ${WRKDIR}/dmd/ini/${OSMODEL}/bin64/dmd.conf ${STAGEDIR}${EXAMPLESDIR}/dmd64.conf

.include <bsd.port.mk>
