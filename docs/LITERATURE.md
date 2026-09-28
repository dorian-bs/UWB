# Literature and Theory Work

This file preserves the references supplied in the project brief and connects them to implementation and experimental decisions. Verify bibliographic metadata against the original papers before final publication.

## Reading priorities

### Experimental foundations

- **Marano et al. [4]:** extract experimental LOS/NLOS features, identification assumptions, mitigation strategy, labels, and evaluation metrics. Use it to challenge the planned transition experiments and LOS/NLOS baselines.
- **Zwirello et al. [3]:** extract architecture, calibration, indoor measurement design, and reported error behavior. Use it to refine hardware characterization and systematic-error controls.
- **Dardari et al. [6]:** extract the physical/statistical sources of multipath ranging error and expected signal observables. Use it to justify which DW3000 diagnostics must be recorded.

### Localization and calibration context

- **Prorok and Martinoli [2]:** extract spatial error modeling and collaboration assumptions. Use it to connect two-node range correction to future localization experiments.
- **Canepa et al. [1]:** extract automatic calibration workflow, mobile-robot assumptions, and evaluation design. Use it when designing antenna-delay calibration and downstream robot experiments.

### Survey and terminology

- **Alarifi et al. [5]:** use for technology context, taxonomy, and comparison with other indoor positioning methods. Prefer primary sources for detailed algorithmic claims.

## Extraction template

For each paper, record:

- Measurement hardware and radio configuration.
- Environment, geometry, motion, obstacles, and sample count.
- Ground-truth method and uncertainty.
- Raw observables and engineered features.
- Noise assumptions and model equations.
- LOS/NLOS label definition and synchronization.
- Train/test split unit and leakage risks.
- Metrics, baselines, uncertainty, and reported limitations.
- Elements reproducible with the present DW3000 setup.
- One concrete decision the paper supports or challenges.

Store notes in a reference manager or separate literature notes; keep only project-level conclusions and decisions here.

## Project bibliography

1. Canepa, A., Talebpour, Z., Savare, S., and Martinoli, A. “Automatic Calibration of Ultra Wide Band Tracking Systems Using a Mobile Robot: A Person Localization Case-study.” Proceedings of the 2017 International Conference on Indoor Positioning and Indoor Navigation, Sapporo, Japan, 2017. DOI: 10.1109/IPIN.2017.8115905.
2. Prorok, A. and Martinoli, A. “Accurate Indoor Localization with Ultra-Wideband using Spatial Models and Collaboration.” *International Journal of Robotics Research*, 33(4):547-568, 2014.
3. Zwirello, L., Schipper, T., Harter, M., and Zwick, T. “UWB Localization System for Indoor Applications: Concept, Realization and Analysis.” *IEEE Transactions on Microwave Theory and Techniques*, 2012.
4. Marano, S., Wesley, G., Wymeersch, H., and Win, M. “NLOS Identification and Mitigation for Localization Based on UWB Experimental Data.” *IEEE Journal on Selected Areas in Communications*, 2010.
5. Alarifi, A., et al. “Ultra Wideband Indoor Positioning Technologies: Analysis and Recent Advances.” *Sensors*, 2016.
6. Dardari, D., Conti, A., Ferner, U., Giorgetti, A., and Win, M. “Ranging with Ultrawide Bandwidth Signals in Multipath Environments.” *Proceedings of the IEEE*, 2009.