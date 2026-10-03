#ifndef HELIOSTAT_CORE_H
#define HELIOSTAT_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

char *heliostat_compute_field_json(const char *input);
char *heliostat_mobile_initialize_gzip(const unsigned char *data, uintptr_t length);
char *heliostat_mobile_request_json(const char *input);
void heliostat_free_string(char *value);

#ifdef __cplusplus
}
#endif

#endif
