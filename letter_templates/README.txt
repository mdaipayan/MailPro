LETTER LIBRARY
==============

The app reads this folder. Each sub-folder is one "letter set" that you can
choose in tab 3 (B · Letters). You then need only the CSV list.

  _shared/                  images used by all sets (letterhead, signature)
  1_paper_setter/           one folder = one letter set (= one "Mail type").
                            The number at the start sets the order on screen.
      Appointment.tex       every .tex file = one letter for each row
      Acceptance_Form.tex   the attachment is named after the file:
      Undertaking.tex         Appointment.tex -> Appointment.pdf
      title.txt             (optional) the name shown as "Mail type",
                              for example: Paper Setter Mail
      email.txt             (optional) the email text for this set
      files/                (optional) shared files named in the list:
                              syllabus PDFs, Guidelines.pdf, Remuneration.pdf ...

TO ADD A NEW LETTER SET
  1. Make a new folder here, for example  4_invigilator_duty
  2. Put your .tex file(s) in it. They must read the list with
       \csvreader[head to column names]{any_name.csv}{}{ ... }
     The CSV name inside the template does not matter.
  3. Put extra images in the same folder, or in _shared/.
  4. Restart the app (or press R in the browser).

TO CHANGE A LETTER
  Edit the .tex file here and reload the page. Nothing else is needed.

email.txt FORMAT
  Subject: your subject with {{ column }} fields
  (blank line)
  the body text ...

KEEP PRIVATE
  _shared/ holds the signature image. Do not put this folder in a public
  GitHub repository. The .gitignore file already leaves the images out.
