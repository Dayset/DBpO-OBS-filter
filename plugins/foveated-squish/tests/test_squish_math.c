/* Prints the mapping for fixed configs; compare_reference.py checks it
 * against research/foveation/squish_math.py (and the JS extension). */
#include <stdio.h>
#include <string.h>
#include "squish-math.h"

int main(void)
{
	const struct squish_axis cases[] = {
		{0.75, 0.15, 0.5, 1.0}, {0.75, 0.15, 0.5, 0.5}, {0.65, 0.15, 0.5, 0.75},
		{0.7, 0.1, 0.35, 1.0},  {0.9, 0.3, 0.6, 2.0},
	};
	for (size_t c = 0; c < sizeof(cases) / sizeof(cases[0]); c++)
		for (int i = 0; i <= 20; i++) {
			double x = i / 20.0;
			printf("%zu %.6f %.12f %.12f %.12f\n", c, x, squish_forward(&cases[c], x),
			       squish_density(&cases[c], x), squish_inverse(&cases[c], x));
		}
	struct squish_config cfg;
	const char *s = "FSQ1:rx=0.75,ry=0.7,cx=0.15,cy=0.2,fx=0.5,fy=0.45,px=1,py=0.75,ar=1.7778";
	char back[256];
	int ok = squish_config_parse(s, &cfg);
	squish_config_format(&cfg, back, sizeof(back));
	fprintf(stderr, "config round trip: %s\n", ok && !strcmp(s, back) ? "PASS" : "FAIL");
	fprintf(stderr, "rejects garbage: %s\n",
		!squish_config_parse("hello", &cfg) && !squish_config_parse("FSQ1:rx=2", &cfg) ? "PASS" : "FAIL");
	return !(ok && !strcmp(s, back));
}
