# Created by: Eric Anholt <anholt@FreeBSD.org>
# $FreeBSD$

PORTNAME=	mesa-libs
PORTVERSION=	${MESAVERSION}
CATEGORIES=	graphics

COMMENT=	OpenGL libraries that support GLX and EGL clients

USE_XORG=	xorgproto x11 xcb xdamage xext \
		xrandr \
		xfixes xshmfence xxf86vm

LIB_DEPENDS+=		libvdpau.so:multimedia/libvdpau
LIB_DEPENDS+=		libwayland-egl.so:graphics/wayland

.include <bsd.port.options.mk>
.include "${.CURDIR:H:H}/graphics/mesa-dri/Makefile.common"

.include <bsd.port.post.mk>
