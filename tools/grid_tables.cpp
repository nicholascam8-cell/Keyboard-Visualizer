/*---------------------------------------------------------*\
|  Processing Code for Keyboard Visualizer                  |
|                                                           |
|  Adam Honse (calcprogrammer1@gmail.com), 12/11/2016       |
\*---------------------------------------------------------*/

// SPDX-License-Identifier: GPL-2.0-only
// Extracted from CalcProgrammer1/KeyboardVisualizer, Visualizer.cpp,
// revision 8424448cc635baca11025aa80d5b85a382e8f91c.
// 2026-11-24: retained both grid functions verbatim and added a table emitter.
#include <algorithm>
#include <iomanip>
#include <iostream>
#include <vector>
#define ROW_IDX_SPECTROGRAPH_TOP 2
#define SPECTROGRAPH_ROWS (64 - ROW_IDX_SPECTROGRAPH_TOP)
#define SPECTROGRAPH_COLS (256)

static void SetupMatrixGrid(int x_count, int y_count, int * x_idx_list, int * y_idx_list)
{
    for(int x = 0; x < x_count; x++)
    {
        if(x_count < 10)
        {
            x_idx_list[x] = (int)((x * (SPECTROGRAPH_COLS / (x_count))) + (0.5f * (SPECTROGRAPH_COLS / (x_count))));
        }
        else if(x < ((x_count) / 2))
        {
            x_idx_list[x] = (int)((x * (SPECTROGRAPH_COLS / (x_count - 1))) + (0.5f * (SPECTROGRAPH_COLS / (x_count - 1))));
        }
        else
        {
            x_idx_list[x] = (int)((x * (SPECTROGRAPH_COLS / (x_count - 1))) - (0.5f * (SPECTROGRAPH_COLS / (x_count - 1))));
        }

    }
    for(int y = 0; y < y_count; y++)
    {
        y_idx_list[y] = (int)(ROW_IDX_SPECTROGRAPH_TOP + (y * (SPECTROGRAPH_ROWS / y_count)) + (0.5f * (SPECTROGRAPH_ROWS / y_count)));
    }
}

static void SetupLinearGrid(int x_count, int * x_idx_list)
{
    if((x_count % 2) == 0)
    {
        //Even number of LEDs
        for(int x = 0; x < x_count; x++)
        {
            x_idx_list[x] = (int)((float)x * (256.0f / (float)x_count)) + (128.0f / (float)x_count);
        }
    }
    else
    {
        //Odd number of LEDs
        for(int x = 0; x < x_count; x++)
        {
            if (x == (x_count / 2))
            {
                x_idx_list[x] = 128;
            }
            else if (x < ((x_count / 2) + 1))
            {
                x_idx_list[x] = (x_count / 2) + ((x + 1) * (256 / (x_count + 1)));
            }
            else
            {
                x_idx_list[x] = ((x_count / 2) + 1) + (x * (256 / (x_count + 1)));
            }

        }
    }
}

// Output is checked into spotify_rgb.py so users need no compiler.
int main()
{
    for (int axis = 0; axis < 3; ++axis)
    {
        const char* names[] = {"MATRIX_X", "MATRIX_Y", "LINEAR_X"};
        std::cout << names[axis] << " = (\n    \"\",\n";
        const int limit = axis == 1 ? 16 : 64;
        for (int size = 1; size <= limit; ++size)
        {
            std::vector<int> values(size);
            int unused = 0;
            if (axis == 0) SetupMatrixGrid(size, 1, values.data(), &unused);
            if (axis == 1) SetupMatrixGrid(1, size, &unused, values.data());
            if (axis == 2) SetupLinearGrid(size, values.data());
            std::cout << "    \"";
            for (int value : values)
                std::cout << std::hex << std::setfill('0') << std::setw(2)
                          << std::clamp(value, 0, 255);
            std::cout << "\",\n";
        }
        std::cout << ")\n";
    }
}
