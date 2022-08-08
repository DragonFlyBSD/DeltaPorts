# Created by: Michael Neumann <mneumann@ntecs.de>
# $FreeBSD$

PORTNAME=	libopensmtpd
PORTVERSION=	0.7
CATEGORIES=	mail

MAINTAINER=	mneumann@ntecs.de
COMMENT=	library for writing opensmtpd filters

LICENSE=	ISCL

MASTER_SITES=	https://distfiles.sigtrap.nl/

LIB_DEPENDS=	libevent.so:devel/libevent

USES=	gmake

CFLAGS+=	-I${LOCALBASE}/include
LDFLAGS+=	-L${LOCALBASE}/lib

post-patch:
	${RM} ${BUILD_WRKSRC}/Makefile
	${MV} ${BUILD_WRKSRC}/Makefile.gnu ${BUILD_WRKSRC}/Makefile

do-install:
	${INSTALL_LIB} ${WRKSRC}/libopensmtpd.so.0.1.0 ${STAGEDIR}${PREFIX}/lib/
	${INSTALL_DATA} ${WRKSRC}/opensmtpd.h ${STAGEDIR}${PREFIX}/include/

post-install:
	${LN} -s libopensmtpd.so.0.1.0 ${STAGEDIR}${PREFIX}/lib/libopensmtpd.so.0
	${LN} -s libopensmtpd.so.0.1.0 ${STAGEDIR}${PREFIX}/lib/libopensmtpd.so

.include <bsd.port.mk>
