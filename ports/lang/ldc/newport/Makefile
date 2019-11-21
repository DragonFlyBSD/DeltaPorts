# $FreeBSD: head/lang/ldc/Makefile 499553 2019-04-21 18:11:40Z antoine $

PORTNAME=		ldc
PORTVERSION=		1.18.0
DISTVERSIONPREFIX=	v
CATEGORIES=		lang

MAINTAINER=		acm@FreeBSD.org
COMMENT=		LLVM-based D compiler

LICENSE=		BSD3CLAUSE

BROKEN_armv6=		fails to compile: Error: undefined identifier _jmp_buf, did you mean alias jmp_buf?
BROKEN_armv7=		fails to compile: Error: undefined identifier _jmp_buf, did you mean alias jmp_buf?
BROKEN_i386=		function core.bitop.bsf (uint v) is not callable using argument types (ulong)
BROKEN_powerpc64=	fails to compile: cc1plus: error: unrecognized command line option "-std=c++11"

LIB_DEPENDS=		libconfig.so:devel/libconfig

# Remarked out llvm flavors, and use one default instead
#FLAVORS=		llvm80 llvm70 llvm60

USES=			ninja cmake:insource compiler:c++14-lang dlang:bootstrap
USE_LDCONFIG=   	yes

CONFLICTS_INSTALL=	ldc

OPTIONS_DEFINE=		LTO BASH_COMPLETION DEBUG FORCE_BOOTSTRAP
OPTIONS_DEFAULT=	LTO

FORCE_BOOTSTRAP_DESC=	Override search for suitable bootstrap d-compiler
FORCE_BOOTSTRAP_BUILD_DEPENDS=${LOCALBASE}/ldc-ltsmaster/bin/ldmd2:lang/ldc-bootstrap

BASH_COMPLETION_DESC=	install bash_completion files
BASH_COMPLETION_DEPENDS=libdata/pkgconfig/bash-completion.pc:shells/bash-completion

DEBUG_DESC=		build with debug enabled
DEBUG_CMAKE_BOOL=	CMAKE_VERBOSE_MAKEFILE
DEBUG_CMAKE_ON=		-DCMAKE_BUILD_TYPE:STRING="RelWithDebInfo"

LTO_DESC=		Enable link time optimization support
LTO_CMAKE_BOOL=		BUILD_LTO_LIBS LDC_INSTALL_LTOPLUGIN

CMAKE_BUILD_TYPE=	Release

PHOBOS_VERSION=		88

# To improve portability of the generated binaries, link the C++ standard library statically.
# Also don't use relax relocations for the C(++) parts of the default libraries in order to
# support older user binutils.
# Note: This build option is more memory intensive
.if exists(/usr/lib32/libc.so)
OPTIONS_DEFINE+=	MULTILIB
OPTIONS_DEFAULT+=	MULTILIB
MULTILIB_DESC=          Build support for 32-bit and 64-bit targets
MULTILIB_CMAKE_BOOL=	MULTILIB LDC_INSTALL_LLVM_RUNTIME_LIBS
MULTILIB_CMAKE_ON=	-DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++" \
			-DRT_CFLAGS="-Wa,-mrelax-relocations=no"
.else
MULTILIB=		"@comment "
.endif

OPTIONS_SUB=		yes

# Remarked out llvm flavors, and use one default instead
#llvm60_PKGNAMEPREFIX=	llvm60-
#llvm60_BUILD_DEPENDS=	llvm60>0:devel/llvm60
#llvm60_RUN_DEPENDS=	llvm60>0:devel/llvm60

#llvm70_PKGNAMEPREFIX=	llvm70-
#llvm70_BUILD_DEPENDS=	llvm70>0:devel/llvm70
#llvm70_RUN_DEPENDS=	llvm70>0:devel/llvm70

#llvm80_PKGNAMEPREFIX=	llvm80-
#llvm80_BUILD_DEPENDS=	llvm80>0:devel/llvm80
#llvm80_RUN_DEPENDS=	llvm80>0:devel/llvm80
BUILD_DEPENDS=  llvm80>0:devel/llvm80
RUN_DEPENDS=    llvm80>0:devel/llvm80

.if !defined({PREFIX)
PREFIX:=             	${LOCALBASE}/${PKGNAMEPREFIX}${PORTNAME}
.endif

CMAKE_INSTALL_PREFIX=	${PREFIX}
PLIST_SUB+=		LOCALBASE=${LOCALBASE} \
			MULTILIB=${MULTILIB} \
			PHOBOS_VERSION=${PHOBOS_VERSION} \
			LLVM_LIB=${LLVM_LIB} \
			FLAVOR=${FLAVOR}

USE_GITHUB=		yes
GH_ACCOUNT=		ldc-developers
GH_PROJECT=		ldc
GH_TUPLE=		ldc-developers:druntime:ad2324e:druntime/runtime/druntime \
			ldc-developers:phobos:b60fda9:phobos/runtime/phobos

# fixes for LTO, gc-sections, backtrace
#PATCH_SITES=		https://github.com/myfreeweb/ldc/commit/
#PATCHFILES=		3616870e9acbe565ff5b59db97482eda010dc00d.patch:-p1 \
#			120ed38cef179ec3774253bb424c159f7a566aa7.patch:-p1 \
#			573cb1456b1074bb4ab604a838658f30beb6a797.patch:-p1

# Use ldc-wrapper.sh to set LD_LIBRARY_PATH before calling the actual binary
# to solve the ldconfig issue which does not recognise libLLVM-X.so format
SUB_FILES=		ldc-wrapper.sh
SUB_LIST=		LLVM_PREFIX="${LOCALBASE}/llvm${LLVM_VER}" LDC_PREFIX="${PREFIX}" LDC_SUFFIX=".bin"

.include <bsd.port.pre.mk>

# Remarked out llvm flavors, and use one default instead
#.if ${FLAVOR}   == "llvm60"
#LLVM_VER?=		60
#D_REQ_LLVM:=		60
#.elif ${FLAVOR} == "llvm70"
#LLVM_VER?=		70
#D_REQ_LLVM:=		70
#.elif ${FLAVOR} == "llvm80"
#LLVM_VER?=		80
#D_REQ_LLVM:=		80
#.endif
LLVM_VER?=		80
D_REQ_LLVM:=		80

.if !defined(LLVM_CONFIG)
LLVM_CONFIG:=		${LOCALBASE}/bin/llvm-config${LLVM_VER}
.endif

.if ${ARCH} == "amd64" || ${ARCH} == "aarch64"
CFLAGS+=		-fPIC
.endif

.if ${OPSYS} == FreeBSD && ${OSVERSION} >= 1200031
EXTRA_PATCHES=	${PATCHDIR}/ino64-*
.endif

.if ${PORT_OPTIONS:MFORCE_BOOTSTRAP}
DCOMPILER_DMD=		${LOCALBASE}/ldc-ltsmaster/bin/ldmd2
.endif

.if defined(WITH_DEBUG) || ${PORT_OPTIONS:MDEBUG}
CMAKE_BUILD_TYPE=	Debug
STRIP=no
.endif

CMAKE_ARGS+=		-DD_COMPILER:PATH="${DCOMPILER_DMD}"
CMAKE_ARGS+=		-DLDC_INSTALL_LLVM_RUNTIME_LIBS:BOOL=ON \
CMAKE_ARGS+=            -DCMAKE_INSTALL_PREFIX:PATH="${PREFIX}"
CMAKE_ARGS+=		-DCMAKE_C_COMPILER:PATH="${CC}"
CMAKE_ARGS+=		-DCMAKE_CXX_COMPILER:PATH="${CXX}"
CMAKE_ARGS+=		-DBUILD_SHARED_LIBS:STRING="BOTH"
CMAKE_ARGS+=		-DLLVM_CONFIG:PATH=${LLVM_CONFIG}

post-patch:
	@${REINPLACE_CMD} -e "s|%%LOCALBASE%%|${LOCALBASE}|" ${WRKSRC}/ldc2_install.conf.in

post-stage:
	@${MKDIR} ${STAGEDIR}${D_INCLUDE_DIR}
	@${MKDIR} ${STAGEDIR}${D_LIB_DIR}

post-install:
	@${MKDIR} ${STAGEDIR}${LOCALBASE}/bin/
	@${MV} ${STAGEDIR}${PREFIX}/bin/ldc2 ${STAGEDIR}${PREFIX}/bin/ldc2.bin
	@${MV} ${STAGEDIR}${PREFIX}/bin/ldmd2 ${STAGEDIR}${PREFIX}/bin/ldmd2.bin
	@${INSTALL_SCRIPT} ${WRKDIR}/ldc-wrapper.sh ${STAGEDIR}${PREFIX}/bin/ldc2
	@${LN} -f ${STAGEDIR}${PREFIX}/bin/ldc2 ${STAGEDIR}${PREFIX}/bin/ldmd2

do-install-BASH_COMPLETION-on:
	@${MKDIR} ${STAGEDIR}${D_INCLUDE_DIR}
	@${MKDIR} ${STAGEDIR}${D_LIB_DIR}
	@${MKDIR} ${STAGEDIR}${PREFIX}/etc/bash_completion.d/
	@${CP} ${WRKSRC}/packaging/bash_completion.d/ldc2 ${STAGEDIR}/${PREFIX}/etc/bash_completion.d/ldc2

.include <bsd.port.post.mk>
