PORTNAME=	xorg-llvm
PORTVERSION=	7.7
PORTREVISION=	2
CATEGORIES=	x11

MAINTAINER=	zrj@ef.irc
COMMENT=	X.Org (llvm enabled) complete distribution metaport

USES=		metaport

# main x11/xorg set
RUN_DEPENDS+=	xorg>0:x11/xorg

# radeon and llvm sofware renderers
RUN_DEPENDS+=	${LOCALBASE}/lib/dri/kms_swrast_dri.so:graphics/dry
RUN_DEPENDS+=	${LOCALBASE}/lib/xorg/modules/drivers/radeon_drv.so:x11-drivers/xf86-video-ati

.include <bsd.port.mk>
