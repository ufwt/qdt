#include <stdint.h>

int main(void) {
    volatile uint32_t a = 0xABCDEF, b = 0x12345678, c = 0, i;

    for(i = 0; i < 20; i++) {
        if(i % 2) {
            // breakpoint lives on the first iteration
            c = b - a; //$br
        }
        else {
            // breakpoint lives on the all iterations
            c = b + a; //$brc
        }

        // Note: value of varible on the one line is checked on the next line

        // check values of the all variables on the first iteration
        c = b & a; //$ch

        // check just value of 'c' on the first iteration
        c = b | a; //$ch.c

        // check just value of 'c' on the all iterations
        c = b ^ a; //$chc.c

        /* combination of commands is possible
            on the first iteration: check line number, values of 'a' and 'b'
            on the all iterations: check value of 'c'
        */
        c = 0; //$br, chc.c, ch.a, ch.b
    }

    // set breakpoint to the end of test
    return 0; //$bre
}
