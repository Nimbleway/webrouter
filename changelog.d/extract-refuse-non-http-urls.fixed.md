`extract()` rejects explicit non-HTTP(S) URLs and HTTP(S) URLs with no host before contacting any backend. Bare domains and host-and-port inputs still reach the backend unchanged.
