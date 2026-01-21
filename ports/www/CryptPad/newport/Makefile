PORTNAME= CryptPad
DISTVERSION= 2024.9.0
PORTREVISION= 1
CATEGORIES= www

MAINTAINER= tyler.clark@foxide.xyz
COMMENT= End-to-end encrypted office suite
WWW=	https://cryptpad.fr/

LICENSE=	AGPLv3
LICENSE_FILE= ${WRKSRC}/LICENSE

RUN_DEPENDS=	node:www/node \
				npm:www/npm \


USE_RC_SUBR=	cryptpad
NO_BUILD=	yes

USE_GITHUB= yes
GH_ACCOUNT= cryptpad
GH_PROJECT= cryptpad
GH_TAGNAME= 0d872af

USERS=		cryptpad
GROUPS=		cryptpad

do-install:
	cd ${WRKSRC} && ${COPYTREE_SHARE} . ${PREFIX}/libexec/cryptpad
	@${ECHO_MSG} "Files installed at: ${DATADIR}"

post-everything:
	${CHOWN} -R ${USERS}:${GROUPS} ${PREFIX}/libexec/cryptpad
	${CP} ${PREFIX}/libexec/cryptpad/config/config.example.js ${PREFIX}/libexec/cryptpad/config/config.js
	npm ci --prefix ${PREFIX}/libexec/cryptpad/
	npm run install:components --prefix ${PREFIX}/libexec/cryptpad

do-test:
	@${ECHO_CMD} "No tests"

.include <bsd.port.mk>
