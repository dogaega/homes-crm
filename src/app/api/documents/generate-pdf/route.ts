import { NextRequest, NextResponse } from 'next/server';
import jsPDF from 'jspdf';
// Note: @react-pdf/renderer imports removed as they're not used in this implementation
import { reportAPIError, measurePerformance, addBreadcrumb } from '@/lib/sentry';

export async function POST(request: NextRequest) {
  return measurePerformance('generatePDF', 'api.request', async () => {
    try {
      addBreadcrumb('PDF generation request started', 'api', 'info');

      const { documentId } = await request.json();

      if (!documentId) {
        addBreadcrumb('PDF generation failed - no document ID', 'api', 'error');
        return NextResponse.json({ error: 'Document ID is required' }, { status: 400 });
      }

      addBreadcrumb('Fetching document for PDF generation', 'api', 'info', { documentId });

      // This route runs server-side (a Next.js Route Handler executing in
      // the Worker), unlike everywhere else `@/lib/api`'s `supabase` shim
      // is used (client components in the browser). That shim builds
      // relative fetch URLs and relies on the browser sending the httpOnly
      // session cookie automatically — neither works from server-side code,
      // so `supabase.from(...)` here always threw and this route always
      // 500'd. Talk to the Worker directly with an absolute URL through the
      // same `/api/backend` rewrite, forwarding the incoming request's
      // Cookie header by hand instead.
      const backendBase = `${new URL(request.url).origin}/api/backend`;
      const cookie = request.headers.get('cookie') || '';

      const docRes = await fetch(`${backendBase}/documents/${documentId}`, {
        headers: { Cookie: cookie },
      });
      if (!docRes.ok) {
        reportAPIError(new Error('Document not found'), {
          endpoint: '/api/documents/generate-pdf',
          method: 'POST',
          status: 404
        });
        return NextResponse.json({ error: 'Document not found' }, { status: 404 });
      }
      const documentRow = await docRes.json();

      let templateRow: any = null;
      if (documentRow.template_id) {
        const templateRes = await fetch(`${backendBase}/document_templates/${documentRow.template_id}`, {
          headers: { Cookie: cookie },
        });
        if (templateRes.ok) templateRow = await templateRes.json();
      }
      const document = { ...documentRow, document_templates: templateRow };

      addBreadcrumb('Generating PDF content', 'api', 'info', {
        documentTitle: document.title,
        templateType: document.document_templates?.document_type
      });

      // Generate PDF content
      // const pdfContent = generatePDFContent(document); // Commented out - not used in current implementation
      
      // Create PDF using jsPDF (simpler approach for now)
      const pdf = new jsPDF();
      
      // Add title
      pdf.setFontSize(20);
      pdf.text(document.title || 'Untitled Document', 20, 30);
      
      // Add content based on template
      let yPos = 50;
      const lineHeight = 10;
      
      // Parse field values and add to PDF
      const fieldValues = document.field_values as Record<string, string>;
      const templateFields = Array.isArray(document.document_templates?.template_fields) 
        ? document.document_templates.template_fields as Array<{ name: string; label: string; type: string }>
        : [];
      
      templateFields.forEach((field: { name: string; label: string; type: string }) => {
        const value = fieldValues[field.name] || '';
        pdf.setFontSize(12);
        pdf.text(`${field.label}: ${value}`, 20, yPos);
        yPos += lineHeight;
        
        // Check if we need a new page
        if (yPos > 250) {
          pdf.addPage();
          yPos = 30;
        }
      });
      
      // Add signature lines
      yPos += 20;
      pdf.setFontSize(10);
      pdf.text('Signatures:', 20, yPos);
      yPos += 15;
      
      pdf.line(20, yPos, 120, yPos); // Signature line
      pdf.text('Client Signature', 20, yPos + 5);
      pdf.text('Date: ___________', 130, yPos + 5);
      
      yPos += 25;
      pdf.line(20, yPos, 120, yPos); // Signature line
      pdf.text('Agent Signature', 20, yPos + 5);
      pdf.text('Date: ___________', 130, yPos + 5);
      
      // Convert to blob and upload to R2 via the Worker (replaces Supabase
      // Storage, which this shim's server-side callers never had access to).
      const pdfBlob = pdf.output('blob');
      const fileName = `document_${documentId}_${Date.now()}.pdf`;

      addBreadcrumb('Uploading PDF to storage', 'api', 'info', { fileName });

      const pdfArrayBuffer = await pdfBlob.arrayBuffer();
      const uploadRes = await fetch(`${backendBase}/documents/upload?filename=${encodeURIComponent(fileName)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/pdf', Cookie: cookie },
        body: pdfArrayBuffer,
      });
      const uploadBody: any = await uploadRes.json().catch(() => null);

      if (!uploadRes.ok || !uploadBody?.url) {
        reportAPIError(new Error(uploadBody?.error || 'PDF upload failed'), {
          endpoint: '/api/documents/generate-pdf',
          method: 'POST',
          status: 500
        });
        return NextResponse.json({ error: 'Failed to upload PDF' }, { status: 500 });
      }

      // Store the same-origin path (proxied through /api/backend to the
      // Worker's /documents/file/:key), so `window.open(pdf_url)` elsewhere
      // in the app hits this app's own origin and the session cookie is
      // sent automatically by the browser, same as any normal navigation.
      const publicUrl = `/api/backend${uploadBody.url}`;

      addBreadcrumb('Updating document with PDF URL', 'api', 'info', { publicUrl });

      const updateRes = await fetch(`${backendBase}/documents/${documentId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', Cookie: cookie },
        body: JSON.stringify({ pdf_url: publicUrl }),
      });

      if (!updateRes.ok) {
        reportAPIError(new Error('Failed to update document with pdf_url'), {
          endpoint: '/api/documents/generate-pdf',
          method: 'POST',
          status: 500
        });
        return NextResponse.json({ error: 'Failed to update document' }, { status: 500 });
      }

      addBreadcrumb('PDF generation completed successfully', 'api', 'info', {
        documentId,
        fileName,
        publicUrl
      });
      
      return NextResponse.json({ pdfUrl: publicUrl });

    } catch (error) {
      reportAPIError(error as Error, {
        endpoint: '/api/documents/generate-pdf',
        method: 'POST',
        status: 500
      });
      return NextResponse.json(
        { error: 'Internal server error' },
        { status: 500 }
      );
    }
  });
}

interface DocumentData {
  title: string;
  field_values: Record<string, string>;
  document_templates: {
    template_fields: Array<{ name: string; label: string; type: string }>;
    name: string;
    template_content?: string;
  };
}

function generatePDFContent(document: DocumentData) {
  const template = document.document_templates;
  const fieldValues = document.field_values;
  
  if (!template?.template_content) {
    return '<p>No template content available</p>';
  }
  
  let content = template.template_content;
  
  // Replace placeholders with actual values
  Object.entries(fieldValues).forEach(([key, value]) => {
    const placeholder = `{{${key}}}`;
    content = content.replace(new RegExp(placeholder, 'g'), String(value || ''));
  });
  
  return content;
}