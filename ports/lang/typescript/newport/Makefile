PORTNAME=	typescript
DISTVERSION=	5.8.2
CATEGORIES=	lang
MASTER_SITES=	https://registry.npmjs.org/${PORTNAME}/-/
DISTNAME=	${PORTNAME}-${DISTVERSION}
EXTRACT_SUFX=	.tgz

MAINTAINER=	desktop@DragonFlyBSD.org
COMMENT=	Language for application-scale JavaScript development
WWW=		https://www.typescriptlang.org/

LICENSE=	APACHE20
LICENSE_FILE=	${WRKSRC}/LICENSE.txt

USES=		nodejs:24,run

NO_ARCH=	yes
NO_BUILD=	yes
WRKSRC=		${WRKDIR}/package

do-install:
	@${MKDIR} ${STAGEDIR}${PREFIX}/lib/node_modules/${PORTNAME}
	cd ${WRKSRC} && ${COPYTREE_SHARE} . ${STAGEDIR}${PREFIX}/lib/node_modules/${PORTNAME}
	${CHMOD} +x ${STAGEDIR}${PREFIX}/lib/node_modules/${PORTNAME}/bin/tsc
	${CHMOD} +x ${STAGEDIR}${PREFIX}/lib/node_modules/${PORTNAME}/bin/tsserver
	${RLN} -s ../lib/node_modules/${PORTNAME}/bin/tsc ${STAGEDIR}${PREFIX}/bin/tsc
	${RLN} -s ../lib/node_modules/${PORTNAME}/bin/tsserver ${STAGEDIR}${PREFIX}/bin/tsserver

.include <bsd.port.mk>
