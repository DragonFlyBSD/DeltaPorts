PORTNAME=	noct
DISTVERSION=	1.1.0
CATEGORIES=	sysutils

USE_GITHUB=	yes
GH_ACCOUNT=	ferusx
GH_PROJECT=	noct
GH_TAGNAME=	v${DISTVERSION}

MAINTAINER=	hedningakjetil@gmail.com
COMMENT=	CLI filesystem inspection and analysis toolkit for Unix-like systems
WWW=		https://github.com/ferusx/noct
LICENSE=	BSD3CLAUSE

USES=		cargo

do-install:
	${MKDIR} ${STAGEDIR}${PREFIX}/bin
	${MKDIR} ${STAGEDIR}${PREFIX}/man/man1
	${MKDIR} ${STAGEDIR}${PREFIX}/share/noct/themes
	${MKDIR} ${STAGEDIR}${PREFIX}/share/noct/themes/console

	${INSTALL_PROGRAM} ${CARGO_TARGET_DIR}/release/noct \
		${STAGEDIR}${PREFIX}/bin/noct
	${RLN} ${STAGEDIR}${PREFIX}/bin/noct \
		${STAGEDIR}${PREFIX}/bin/nt

	${INSTALL_MAN} ${WRKSRC}/man/man1/noct.1 \
		${STAGEDIR}${PREFIX}/man/man1/noct.1
	${GZIP_CMD} ${STAGEDIR}${PREFIX}/man/man1/noct.1
	${RLN} ${STAGEDIR}${PREFIX}/man/man1/noct.1.gz \
		${STAGEDIR}${PREFIX}/man/man1/nt.1.gz

	${INSTALL_DATA} ${WRKSRC}/themes/themes/*.toml \
		${STAGEDIR}${PREFIX}/share/noct/themes/
	${INSTALL_DATA} ${WRKSRC}/themes/themes/README.md \
		${STAGEDIR}${PREFIX}/share/noct/themes/

	${INSTALL_DATA} ${WRKSRC}/themes/themes/console/*.toml \
		${STAGEDIR}${PREFIX}/share/noct/themes/console/
	${INSTALL_DATA} ${WRKSRC}/themes/themes/console/README.md \
		${STAGEDIR}${PREFIX}/share/noct/themes/console/
		
.include "Makefile.crates"
.include <bsd.port.mk>
