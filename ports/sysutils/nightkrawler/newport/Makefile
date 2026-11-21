# $DragonFly BSD$

PORTNAME=	nightkrawler
DISTVERSION=	0.4.5
CATEGORIES=	sysutils

USE_GITHUB=	yes
GH_ACCOUNT=	ferusx
GH_PROJECT=	nightkrawler
GH_TAGNAME=	v${DISTVERSION}

MAINTAINER=	hedningakjetil@gmail.com
COMMENT=	Filesystem duplicate finder and removal tool for Unix-like systems
WWW=		https://github.com/ferusx/nightkrawler
LICENSE=	BSD3CLAUSE

USES=		cargo

do-install:
	${MKDIR} ${STAGEDIR}${PREFIX}/bin
	${MKDIR} ${STAGEDIR}${PREFIX}/man/man1

	${INSTALL_PROGRAM} ${CARGO_TARGET_DIR}/release/nightkrawler \
		${STAGEDIR}${PREFIX}/bin/nightkrawler
	${RLN} ${STAGEDIR}${PREFIX}/bin/nightkrawler \
		${STAGEDIR}${PREFIX}/bin/nk

	${INSTALL_MAN} ${WRKSRC}/man/man1/nightkrawler.1 \
		${STAGEDIR}${PREFIX}/man/man1/nightkrawler.1
	${GZIP_CMD} ${STAGEDIR}${PREFIX}/man/man1/nightkrawler.1
	${RLN} ${STAGEDIR}${PREFIX}/man/man1/nightkrawler.1.gz \
		${STAGEDIR}${PREFIX}/man/man1/nk.1.gz

.include "Makefile.crates"
.include <bsd.port.mk>
