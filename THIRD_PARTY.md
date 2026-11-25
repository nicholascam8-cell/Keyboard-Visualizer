# Licensing and build provenance

The combined visualizer is distributed under GNU GPL version 2, without warranty. The complete terms are in LICENSE.

Adam Honse (CalcProgrammer1) wrote the upstream KeyboardVisualizer grid routines. The two function bodies in tools/grid_tables.cpp are verbatim from [Visualizer.cpp](https://github.com/CalcProgrammer1/KeyboardVisualizer/blob/8424448cc635baca11025aa80d5b85a382e8f91c/KeyboardVisualizerCommon/Visualizer.cpp#L1448-L1502). The sampling dimensions come from VisualizerDefines.h at the same revision.

On 2026-11-24 the two routines were extracted into a standalone table generator. Its wrapper bounds samples to valid pixel coordinates. The Python visualizer was modified to enumerate keyboards, interpret zone layouts, and consume the checked-in tables. Wider grids and taller matrices use evenly spaced centres. Users running the Python script do not need the generator or a C++ compiler.

To reproduce the lookup tables on a development machine:

```sh
c++ -std=c++17 tools/grid_tables.cpp -o /tmp/grid_tables
/tmp/grid_tables
```

The output is the MATRIX_X, MATRIX_Y and LINEAR_X declarations in spotify_rgb.py. Distribute matching source, including this generator, alongside packaged releases. External dependencies retain their own licenses.
