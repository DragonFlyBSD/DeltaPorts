dfly-patch:
	${REINPLACE_CMD} -e 's/foo/bar/' ${WRKSRC}/file
	@echo done
