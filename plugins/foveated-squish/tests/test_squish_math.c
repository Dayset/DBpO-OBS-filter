/* Prints the mapping for fixed configs; compare_reference.py checks it
 * against research/foveation/squish_math.py (and the JS extension). */
#include <stdio.h>
#include <string.h>
#include "squish-math.h"
#include "look_golden.h"

int main(void)
{
	const struct squish_axis cases[] = {
		{0.75, 0.15, 0.5, 1.0}, {0.75, 0.15, 0.5, 0.5}, {0.65, 0.15, 0.5, 0.75},
		{0.7, 0.1, 0.35, 1.0},  {0.9, 0.3, 0.6, 2.0},   {0.25, 0.05, 0.5, 0.1},
	};
	for (size_t c = 0; c < sizeof(cases) / sizeof(cases[0]); c++)
		for (int i = 0; i <= 20; i++) {
			double x = i / 20.0;
			printf("%zu %.6f %.12f %.12f %.12f\n", c, x, squish_forward(&cases[c], x),
			       squish_density(&cases[c], x), squish_inverse(&cases[c], x));
		}
	struct squish_config cfg = {0};
	const char *s = "FSQ1:rx=0.75,ry=0.7,cx=0.15,cy=0.2,fx=0.5,fy=0.45,px=1,py=0.75,ar=1.7778";
	char back[256];
	int ok = squish_config_parse(s, &cfg);
	squish_config_format(&cfg, back, sizeof(back));
	ok = ok && !strcmp(s, back);
	fprintf(stderr, "config round trip: %s\n", ok ? "PASS" : "FAIL");
	fprintf(stderr, "rejects garbage: %s\n",
		!squish_config_parse("hello", &cfg) && !squish_config_parse("FSQ1:rx=2", &cfg) ? "PASS" : "FAIL");
	const char *sb =
		"FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778,ox=0.125,oy=0.118519,bw=0.75,bh=0.75";
	int okb = squish_config_parse(sb, &cfg) && cfg.boxed;
	squish_config_format(&cfg, back, sizeof(back));
	okb = okb && !strcmp(sb, back) && !squish_config_parse("FSQ1:rx=0.75,ox=0.3,bw=0.75", &cfg);
	fprintf(stderr, "boxed config round trip: %s\n", okb ? "PASS" : "FAIL");
	/* Viewer look: old keys still load (clamped to 0..1), new strings carry a look code, and
	 * old strings load with the look off. */
	const char *sl =
		"FSQ1:rx=0.75,ry=0.75,cx=0.15,cy=0.15,fx=0.5,fy=0.5,px=1,py=1,ar=1.7778,ls=0.5,lc=0.25,lh=0.1,lb=0.15,lp=0.6";
	int okl = squish_config_parse(sl, &cfg) && cfg.look.sharpen == 0.5 && cfg.look.sharpen_plus == 0.6;
	squish_config_format(&cfg, back, sizeof(back));
	struct squish_config again;
	okl = okl && strstr(back, "\xe2\x80\xbb") && !strstr(back, "ls=") && squish_config_parse(back, &again) &&
	      fabs(again.look.sharpen - 0.5) < 0.5 / 127 + 1e-9 && fabs(again.look.sharpen_plus - 0.6) < 0.5 / 127 + 1e-9 &&
	      fabs(again.x.r - 0.75) < 1e-9;
	okl = okl && squish_config_parse("FSQ1:rx=0.75,ar=1.7778,lb=7,lc=-1", &cfg) && cfg.look.bloom == 1.0 &&
	      cfg.look.clarity == 0.0;
	okl = okl && squish_config_parse(s, &cfg) && !squish_look_active(&cfg.look);
	squish_config_format(&cfg, back, sizeof(back));
	okl = okl && !strcmp(s, back);
	fprintf(stderr, "viewer look round trip: %s\n", okl ? "PASS" : "FAIL");

	/* Look code vs the Python reference (color_math.py golden cases). */
	int okc = 1;
	for (size_t i = 0; i < sizeof(look_golden) / sizeof(look_golden[0]); i++) {
		struct squish_look l;
		memcpy(&l, look_golden[i].look, sizeof(l));
		char code[SQUISH_LOOK_CODE_MAX];
		const size_t n = squish_look_pack(&l, code, sizeof(code));
		okc = okc && !strcmp(code, look_golden[i].code) && n == strlen(look_golden[i].code);
		struct squish_look d;
		if (n) {
			okc = okc && squish_look_unpack(code, &d);
			if (okc) {
				const double *got = (const double *)&d, *want = look_golden[i].decoded;
				for (int k = 0; k < 18; k++)
					okc = okc && fabs(got[k] - want[k]) < 1e-12;
			}
		}
	}
	struct squish_look junk;
	okc = okc && !squish_look_unpack("abc", &junk) && !squish_look_unpack("\xe2\x80\xbb\xe4\xb8\x80", &junk);
	fprintf(stderr, "look code vs Python: %s\n", okc ? "PASS" : "FAIL");
	return !(ok && okb && okl && okc);
}
