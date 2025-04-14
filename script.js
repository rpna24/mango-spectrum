async function fetchData() {
    const lat = document.getElementById("latitude").value;
    const lon = document.getElementById("longitude").value;
    const radius = document.getElementById("radius").value;
    const minFreq = document.getElementById("min_freq").value;
    const maxFreq = document.getElementById("max_freq").value;

    const url = `/getData?latitude=${lat}&longitude=${lon}&radius=${radius}&min_freq=${minFreq}&max_freq=${maxFreq}`;
    const response = await fetch(url);
    const data = await response.json();

    console.log("📊 Received data:", data);

    if (!data.length) {
        Plotly.newPlot("frequency-plot", [{
            type: 'bar',
            orientation: 'h',
            y: ['No Data'],
            x: [1],
            marker: { color: 'red' },
            name: "No Matches"
        }], {
            title: "No Data Found",
            xaxis: { title: "Frequency (MHz)" },
            yaxis: { title: "Device Type" },
            height: 400
        });
        return;
    }

    const traces = data.map(row => ({
        type: 'bar',
        orientation: 'h',
        y: [row.Device_Type === 'T' ? "Transmitter" : "Receiver"],
        x: [row.MaxFrequency - row.MinFrequency],
        base: row.MinFrequency,
        name: `${row.Frequency} MHz / ${row.Bandwidth_kHz} kHz`,
        marker: {
            color: row.Device_Type === 'T' ? '#00c6ff' : '#28a745',
            opacity: 0.8
        },
        text: `Freq: ${row.Frequency} MHz<br>BW: ${row.Bandwidth_kHz} kHz`,
        hoverinfo: 'text'
    }));

    const layout = {
        title: 'Spectrum Allocation',
        barmode: 'stack',
        xaxis: { title: 'Frequency (MHz)' },
        yaxis: { title: 'Device Type', type: 'category' },
        height: 600,
        showlegend: false
    };

    Plotly.newPlot("frequency-plot", traces, layout);
}