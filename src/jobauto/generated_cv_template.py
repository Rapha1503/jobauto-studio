GENERATED_CV_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=1.45cm]{geometry}
\usepackage[hidelinks]{hyperref}
\usepackage{enumitem}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\setlength{\parindent}{0pt}
\pagestyle{empty}
\setlist[itemize]{leftmargin=1.25em,itemsep=1.5pt,topsep=3pt,parsep=0pt}
\newcommand{\cvsection}[1]{\vspace{6pt}{\large\textbf{#1}}\par\vspace{3pt}\hrule\vspace{4pt}}
\begin{document}
%%JOBAUTO_BODY%%
\end{document}
"""


def generated_cv_template_bytes() -> bytes:
    return GENERATED_CV_TEMPLATE.encode("utf-8")
