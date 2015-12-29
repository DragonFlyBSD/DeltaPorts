# Created by: John Marino <marino@FreeBSD.org>
# $FreeBSD$

PORTNAME=	synth
PORTVERSION=	0.90
CATEGORIES=	ports-mgmt

MAINTAINER=	marino@FreeBSD.org
COMMENT=	Custom package repository builder for FreeBSD and DragonFly

IGNORE=		port is work in progress

LICENSE=	ISCL
LICENSE_FILE=	${WRKSRC}/License.txt

BUILD_DEPENDS=	${LOCALBASE}/lib/gnat/util.gpr:${PORTSDIR}/devel/ada-util \
		${LOCALBASE}/lib/gnat/${IFM}.gpr:${PORTSDIR}/misc/${IFM}

USES=		ada:6
USE_GITHUB=	yes
GH_ACCOUNT=	jrmarino
GH_TAGNAME=	1e39801

IFM=		ini_file_manager

PLIST_FILES=	bin/synth libexec/synthexec #man/man1/synth.1.gz

# When framework is moved to Ada6, the ada-util and ini-file-manager
# libraries can be used as prebuilt (switch default.gpr url)

post-patch:
	${MKDIR} ${WRKSRC}/external/include \
		${WRKSRC}/external/lib/gnat \
		${WRKSRC}/external/lib/util.static \
		${WRKSRC}/external/lib/${IFM} \
		${WRKSRC}/build
	(cd ${LOCALBASE}/include && \
		${COPYTREE_SHARE} util.static ${WRKSRC}/external/include && \
		${COPYTREE_SHARE} ${IFM} ${WRKSRC}/external/include)
	${INSTALL_DATA} ${LOCALBASE}/lib/gnat/util.gpr \
		${WRKSRC}/external/lib/gnat
	${INSTALL_DATA} ${LOCALBASE}/lib/gnat/${IFM}.gpr \
		${WRKSRC}/external/lib/gnat
	${REINPLACE_CMD} -e "/Externally_Built/ s|[tT]rue|false|" \
		${WRKSRC}/external/lib/gnat/*.gpr

do-build:
	(cd ${WRKSRC} && ${SETENV} ${MAKE_ENV} gnatmake -P synthexec)
	(cd ${WRKSRC} && ${SETENV} ${MAKE_ENV} gnatmake -P default)

do-install:
	${INSTALL_PROGRAM} ${WRKSRC}/build/synth \
		${STAGEDIR}${PREFIX}/bin
	${INSTALL_PROGRAM} ${WRKSRC}/build/synthexec \
		${STAGEDIR}${PREFIX}/libexec

.include <bsd.port.mk>
