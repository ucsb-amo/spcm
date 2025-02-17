"""
Spectrum Instrumentation GmbH (c)

4_scapp_acq_fifo_fft.py

Example that shows how to combine the CUDA DMA transfer with the acquisition of data. The example uses FIFO recording mode
to acquire data then send the data through rdma to the GPU using the SCAPP add-on, which takes the Fast-Fourier-Transform (FFT)
of the data and sends it back to the host memory. On the host memory the data is plotted continuously, using matplotlib.

For analog recording cards (digitizers) for the the M2p, M4i, M4x and M5i card-families.

See the README file in the parent folder of this examples directory for information about how to use this example.

See the LICENSE file for the conditions under which this software may be used and distributed.
"""


import spcm
from spcm import units

import numpy as np
import matplotlib.pyplot as plt
import cupy as cp

card : spcm.Card

# with spcm.Card('/dev/spcm0') as card:                         # if you want to open a specific card
# with spcm.Card('TCPIP::192.168.1.10::inst0::INSTR') as card:  # if you want to open a remote card
# with spcm.Card(serial_number=12345) as card:                  # if you want to open a card by its serial number
with spcm.Card(card_type=spcm.SPCM_TYPE_AI) as card:            # if you want to open the first card of a specific type

    # do a simple standard setup
    card.card_mode(spcm.SPC_REC_FIFO_SINGLE)     # single trigger standard mode
    card.timeout(5 * units.s)

    # setup trigger engine
    trigger = spcm.Trigger(card)
    trigger.or_mask(spcm.SPC_TMASK_SOFTWARE)

    # setup channels
    channels = spcm.Channels(card, card_enable=spcm.CHANNEL0)
    amplitude = channels[0].amp(1 * units.V, return_unit=units.V)
    amplitude_magnitude_V = amplitude.to(units.V).magnitude
    max_value = card.max_sample_value()

    # we try to use the max samplerate
    clock = spcm.Clock(card)
    clock.mode(spcm.SPC_CM_INTPLL)
    sample_rate = clock.sample_rate(10 * units.MHz, return_unit=(units.MHz))
    print(f"Used Sample Rate: {sample_rate}")
    
    # Setup a data transfer object with CUDA DMA
    notify_samples = 64 * units.KiS
    notify_samples_magnitude = notify_samples.to_base_units().magnitude
    num_samples = 8 * units.MiS

    scapp_transfer = spcm.SCAPPTransfer(card, direction=spcm.Direction.Acquisition)
    scapp_transfer.notify_samples(notify_samples)
    scapp_transfer.allocate_buffer(num_samples)
    scapp_transfer.start_buffer_transfer()
    
    # length of FFT result
    num_fft_samples = notify_samples_magnitude // 2 + 1

    # allocate memory on GPU
    data_volt_gpu = cp.zeros(notify_samples_magnitude, dtype = cp.float32)
    spectrum_gpu = cp.zeros(num_fft_samples, dtype = cp.float32)

    # elementwise kernel to convert the raw data to volts
    kernel_signal_to_volt = cp.ElementwiseKernel(
        'T rawData, float64 voltPerLSB', # two inputs: rawData is the integer data (template; can be int8, int16 and int32), voltPerLSB is the factor to convert to volts
        'float32 convertedData', # output is a float32
        'convertedData = rawData * voltPerLSB', # the conversion operation
        'signal_to_volt') # name of the kernel
    factor_signal_to_volt = amplitude.to(units.V).magnitude / max_value

    # elementwise kernel to convert the FFT data to a spectrum in dBFS
    kernel_fft_to_spectrum = cp.ElementwiseKernel(
        'complex64 fftData, int64 numElem, float32 fIR_V', # 3 inputs: complex fft input data; number of samples; input voltage range
        'float32 spectrumData', # output: the spectrum in dBFS
        'spectrumData = 20.0f * log10f ( abs(fftData / thrust::complex<float>(numElem / 2.0f + 1.0f, 0.0f)) / fIR_V)', # the conversion
        'fft_to_spectrum' # name of the conversion
    )
    
    # plot function
    plot_divider = 10 # plot 1 in 10 data blocks
    fig, ax = plt.subplots()
    freq = np.fft.rfftfreq(notify_samples_magnitude, 1/sample_rate)
    line, = ax.plot(freq, np.zeros_like(freq))
    ax.set_ylim([-160.0, 10.0])  # range of Y axis
    ax.xaxis.set_units(units.MHz)
    plt.show(block=False)
    plt.draw()

    card.start(spcm.M2CMD_CARD_ENABLETRIGGER | spcm.M2CMD_DATA_STARTDMA)

    counter = 0
    for data_raw_gpu in scapp_transfer:
        # waits for a block to become available after the data is transferred from the card to the gpu memory using scapp

        # ... this is the point to do anything with the data on the gpu

        # start kernel on the gpu to process the transfered data
        kernel_signal_to_volt(data_raw_gpu, factor_signal_to_volt, data_volt_gpu)
        
        # calculate the FFT
        fftdata_gpu = cp.fft.rfft(data_volt_gpu)

        # scale the FFT result
        kernel_fft_to_spectrum(fftdata_gpu, notify_samples_magnitude, amplitude_magnitude_V, spectrum_gpu)

        # after kernel has finished we copy processed data from the gpu to the host cpu
        spectrum_cpu = cp.asnumpy(spectrum_gpu)
 
        # now the processed data is in the host memory and can be used for plotting
        if counter % plot_divider == 0:
            line.set_ydata(spectrum_cpu)
            fig.canvas.draw()
            fig.canvas.flush_events()
        counter += 1





