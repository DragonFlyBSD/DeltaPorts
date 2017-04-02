--- modules/sipcapture/sipcapture.c.orig	2017-02-23 11:15:50 UTC
+++ modules/sipcapture/sipcapture.c
@@ -1895,7 +1895,9 @@ set_generic_hep_chunk(struct hepv3* h3,
 				if (LOWER_WORD(data->s[2], data->s[3]) != LOWER_WORD('t', 'p'))
 					RETURN_ERROR("invalid proto %.*s\n", data->len, data->s);
 
+#ifndef __DragonFly__
 				h3->hg.ip_proto.data = PROTO_SCTP;
+#endif
 				break;
 			case LOWER_WORD('w','s'):
 				h3->hg.ip_proto.data = PROTO_WS;
